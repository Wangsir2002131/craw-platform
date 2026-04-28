# 问题内容，片段，web
# 和afu一样的逻辑
# & "C:\Program Files\Google\Chrome\Application\chrome.exe" --user-data-dir="D:\doubao_real_profiles\account_1"
import random
import os
import sys
import time
import threading
import logging
import traceback
import zipfile
from typing import List, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import pymysql
except ImportError:
    pymysql = None

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from selenium.common.exceptions import TimeoutException, NoSuchElementException
except ImportError:
    webdriver = None
    By = None
    Keys = None
    WebDriverWait = None
    EC = None
    Service = None
    Options = None
    TimeoutException = Exception
    NoSuchElementException = Exception

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), 'shared-methods'))
try:
    from shared_methods import dp_api_afu, send_dingtalk_message, DB_CONFIG, get_proxy
except Exception:
    dp_api_afu = None
    DB_CONFIG = {}

    def send_dingtalk_message(*args, **kwargs):
        return None

    def get_proxy(*args, **kwargs):
        return None

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
try:
    from platform.account.account_allocator import AccountAllocator
except Exception:
    AccountAllocator = None

try:
    from database_usage_example import (
        insert_task_question_reply_content,
        insert_task_question_reply_content_snippet,
        insert_product_llm_task_web_content,
        insert_task_question_search_web,
        update_product_llm_task_status,
        get_default_product_llm_task_id
    )
except Exception:
    def insert_task_question_reply_content(*args, **kwargs):
        return None

    def insert_task_question_reply_content_snippet(*args, **kwargs):
        return None

    def insert_product_llm_task_web_content(*args, **kwargs):
        return None

    def insert_task_question_search_web(*args, **kwargs):
        return None

    def update_product_llm_task_status(*args, **kwargs):
        return None

    def get_default_product_llm_task_id():
        return None

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# 屏蔽 urllib3 连接池满的警告
logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)

# 全局 profile 占用锁，防止并发重试时多个任务争抢同一个 profile
_profile_lock = threading.Lock()
_profiles_in_use: set = set()


class DouBao:

    def __init__(self, account_id: str, use_proxy: bool = True, proxy_api: Optional[Callable] = None, executable_path: Optional[str] = None):
        self.browser = None
        self.page = None
        self.context = None
        self.account_id = account_id
        self.use_proxy = use_proxy
        self.proxy_api = proxy_api
        self.proxy = None
        self.executable_path = executable_path
        self.currentPath = os.path.dirname(os.path.realpath(__file__))
        self.lock = threading.Lock()
        self.product_llm_task_id = get_default_product_llm_task_id()  # 默认 ProductLlmTaskId
        self.search_web_id_map = {}  # 引用编号到 SearchWebId 的映射
        self.total_errors = 0
        self.total_errors_num = 0
        self.background_threads = []

    def record_error(self, question_name, error_msg=""):
        """记录一次错误"""
        self.total_errors += 1
        logger.warning(f"连续错误次数: {self.total_errors}/3")

        if self.total_errors == 3:
            self.total_errors = 0
            self.total_errors_num += 1
            logger.warning(f"严重错误累计次数: {self.total_errors_num}/3")

            if self.total_errors_num >= 2:
                logger.error("🚨 连续严重错误达到阈值，发送告警")
                content_text = f"""平台：doubao \n
                账号: {self.account_id} \n
                问题: {question_name} \n
                错误: {error_msg} \n
                连续严重错误次数: {self.total_errors_num}"""
                send_dingtalk_message(content_text)
                self.total_errors = 0
                self.total_errors_num = 0

    def doubao_scroll_and_load_refs(self, max_round=15):
        wait = WebDriverWait(self.driver, 10)
        # 找到真正滚动容器
        scroll_box = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, '[data-testid="message-list"]')))
        logger.info("开始执行 DouBao 引用加载滚动逻辑")
        for i in range(max_round):
            logger.info(f"滚动轮次: {i + 1}")
            # 1️⃣ 回到顶部
            self.driver.execute_script("arguments[0].scrollTop = -999999", scroll_box)
            time.sleep(2)
            # 2️⃣ 滚到底部
            self.driver.execute_script("arguments[0].scrollTop = 0", scroll_box)
            time.sleep(2)
            # 3️⃣ 再回到顶部
            self.driver.execute_script("arguments[0].scrollTop = -999999", scroll_box)
            time.sleep(2)
            # 4️⃣ 判断引用是否加载完成
            try:
                btn = self.driver.find_element(By.ID, "to-bottom-button")
                visible = btn.get_attribute("data-visible")
                logger.info(f"to-bottom-button data-visible = {visible}")
                if visible == "true":
                    logger.info("检测到引用已加载完成")
                    # 最终停留在顶部
                    self.driver.execute_script("arguments[0].scrollTop = -999999", scroll_box)
                    time.sleep(2)
                    break
            except Exception:
                logger.info("未找到 to-bottom-button，继续滚动")
        logger.info("滚动流程结束")

    def get_profile_dir(self) -> str:
        """
        必须是【人工 Chrome】创建并登录过的目录
        """
        base = r"D:/doubao_real_profiles"
        path = os.path.join(base, f"account_{self.account_id}")
        if not os.path.exists(path):
            os.makedirs(path, exist_ok=True)
            logger.info(f"创建 Profile 目录: {path}")
        return path

    def get_proxy_config(self):
        """获取代理配置（如果配置了代理 API 则调用）"""
        if self.proxy_api:
            try:
                proxy = self.proxy_api()
                logger.info(f"✓ 获取到代理：{proxy.get('server', 'unknown') if proxy else 'None'}")
                return proxy
            except Exception as e:
                logger.error(f"✗ 获取代理失败：{e}")
                return None
        # 如果没有配置代理 API，使用共享方法中的固定代理
        try:
            proxy = get_proxy()
            logger.info(f"✓ 获取到固定代理：{proxy.get('server', 'unknown') if proxy else 'None'}")
            return proxy
        except Exception as e:
            logger.error(f"✗ 获取固定代理失败：{e}")
            return None

    def create_driver(self, profile_dir: str = None):
        """创建浏览器实例（支持代理）- 使用持久化 Profile 目录"""
        if profile_dir is None:
            profile_dir = self.get_profile_dir()

        options = Options()
        options.add_argument(f"--user-data-dir={profile_dir}")
        options.add_argument("--profile-directory=Default")
        options.add_argument("--start-maximized")
        options.add_argument("--disable-infobars")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)

        # 代理配置
        if self.use_proxy:
            if self.proxy_api:
                self.proxy = self.proxy_api()
            else:
                try:
                    self.proxy = get_proxy()
                except:
                    self.proxy = None

            if self.proxy and 'server' in self.proxy:
                server = self.proxy['server'].replace('http://', '').replace('https://', '')
                if '@' in server:
                    auth, server = server.split('@')
                    user, pwd = auth.split(':')
                else:
                    user = self.proxy.get('username')
                    pwd = self.proxy.get('password')

                if ':' in server:
                    host, port = server.split(':')
                else:
                    host, port = server, '80'

                if user and pwd:
                    logger.info(f"✓ 已开启代理(带认证): {host}:{port}")
                    extension_path = self.create_proxy_auth_extension(host, port, user, pwd)
                    options.add_extension(extension_path)
                else:
                    logger.info(f"✓ 已开启代理(无认证): {host}:{port}")
                    options.add_argument(f'--proxy-server={host}:{port}')
        else:
            logger.info("✗ 代理已关闭")

        if self.executable_path:
            options.binary_location = self.executable_path

        chromedriver_path = r"C:/Program Files/Google/Chrome/Application/chromedriver.exe"
        service = Service(chromedriver_path)
        try:
            driver = webdriver.Chrome(service=service, options=options)
            time.sleep(random.randint(5, 8))
            driver.get("https://www.doubao.com/chat/")
            WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.TAG_NAME, "textarea")))
            time.sleep(random.randint(5, 8))
            logger.info(f"✓ Chrome 启动成功 (Profile: {profile_dir})")
            return driver
        except Exception as e:
            logger.error(f"浏览器启动失败：{e}")
            raise

    def create_proxy_auth_extension(self, proxy_host, proxy_port, proxy_user, proxy_pass):
        """在本地创建一个临时插件用于代理账密认证"""
        manifest_json = """
        {
            "version": "1.0.0",
            "manifest_version": 2,
            "name": "Chrome Proxy",
            "permissions": [
                "proxy",
                "tabs",
                "unlimitedStorage",
                "storage",
                "<all_urls>",
                "webRequest",
                "webRequestBlocking"
            ],
            "background": {
                "scripts": ["background.js"]
            },
            "minimum_chrome_version":"22.0.0"
        }
        """
        background_js = f"""
        var config = {{
                mode: "fixed_servers",
                rules: {{
                  singleProxy: {{
                    scheme: "http",
                    host: "{proxy_host}",
                    port: parseInt({proxy_port})
                  }},
                  bypassList: ["localhost"]
                }}
              }};
        chrome.proxy.settings.set({{value: config, scope: "regular"}}, function() {{}});
        chrome.webRequest.onAuthRequired.addListener(
                    function(details) {{
                        return {{
                            authCredentials: {{
                                username: "{proxy_user}",
                                password: "{proxy_pass}"
                            }}
                        }};
                    }},
                    {{urls: ["<all_urls>"]}},
                    ["blocking"]
        );
        """

        plugin_path = os.path.join(self.currentPath, f"./proxy_auth_plugin/proxy_auth_plugin_{self.account_id}.zip", )
        os.makedirs(os.path.dirname(plugin_path), exist_ok=True)
        with zipfile.ZipFile(plugin_path, "w") as zp:
            zp.writestr("manifest.json", manifest_json)
            zp.writestr("background.js", background_js)
        return plugin_path

    def ensure_logged_in(self, driver):
        """检查是否登录"""
        try:
            WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.TAG_NAME, "textarea")))
            logger.info(f"✅ 账号 {self.account_id} 登录态校验通过")
            return True
        except TimeoutException:
            raise RuntimeError(f"❌ 账号 {self.account_id} 未登录豆包，请先用【真实 Chrome】人工登录并保存登录状态")

    def run_single(self, question: dict, round_num: int = 1):
        """为单个问题创建独立浏览器实例并执行，round_num 表示第几遍"""
        current_profile = f"account_{self.account_id}"
        with _profile_lock:
            _profiles_in_use.add(current_profile)
        try:
            self.driver = self.create_driver()
            try:
                self.ensure_logged_in(self.driver)
                self.interact_with_page(
                    question['QuestionId'],
                    question['QuestionName'],
                    question['ProductLlmTaskId'],
                    round_num
                )
                if self.background_threads:
                    for thread in self.background_threads:
                        thread.join()
            finally:
                if self.driver:
                    try:
                        self.driver.quit()
                    except:
                        pass
                    self.driver = None
        finally:
            with _profile_lock:
                _profiles_in_use.discard(current_profile)

    def interact_with_page(self, question_id: str, question_name: str, product_llm_task_id: str, round_num: int = 1):
        """与网页进行交互的逻辑（包含重试和 Profile 切换）"""
        # 获取所有可用的 profile 目录
        profile_base = r"D:/doubao_real_profiles"
        if os.path.exists(profile_base):
            all_profiles = [d for d in os.listdir(profile_base) if d.startswith("account_")]
        else:
            all_profiles = [f"account_{self.account_id}"]

        if not all_profiles:
            all_profiles = [f"account_{self.account_id}"]

        # 尝试当前 profile
        profiles_to_try = []
        current_profile = f"account_{self.account_id}"
        if current_profile in all_profiles:
            profiles_to_try.append(current_profile)
        profiles_to_try.extend([p for p in all_profiles if p != current_profile])

        # 最多重试所有可用的 profiles
        for idx, profile_name in enumerate(profiles_to_try):
            # 跳过其他任务正在占用的 profile（当前任务自身的 profile 除外）
            if idx > 0:
                with _profile_lock:
                    if profile_name in _profiles_in_use:
                        logger.info(f"⏭️ Profile [{profile_name}] 正被其他任务占用，跳过")
                        continue
                    _profiles_in_use.add(profile_name)
            profile_dir = os.path.join(profile_base, profile_name)
            logger.info(f"尝试使用 Profile [{idx + 1}/{len(profiles_to_try)}]: {profile_name}")

            if idx == 0:
                # 第一次尝试，使用当前的浏览器实例
                try:
                    self._interact_with_page_core(question_id, question_name, product_llm_task_id, round_num)
                    logger.info(f"✅ 问题回答成功 [ID: {question_id}] 使用 Profile: {profile_name}")
                    # 🔥 成功后重置计数器
                    self.total_errors = 0
                    self.total_errors_num = 0
                    return  # 成功则直接返回
                except Exception as e:
                    logger.error(f"❌ 使用当前浏览器回答失败 [ID: {question_id}]: {e}")
                    self.record_error(question_name, str(e))

                    try:
                        if self.driver:
                            self.driver.quit()
                            self.driver = None
                    except:
                        pass
            else:
                # 后续尝试，需要创建新的浏览器实例
                try:
                    logger.info(f"🔄 切换到新 Profile 重试：{profile_name}")
                    success = self._interact_with_page_with_profile(question_id, question_name, product_llm_task_id, profile_dir, round_num)
                    if success:
                        logger.info(f"✅ 问题回答成功 [ID: {question_id}] 使用新 Profile: {profile_name}")
                        # 🔥 成功后重置计数器
                        self.total_errors = 0
                        self.total_errors_num = 0
                        return  # 成功则直接返回
                    else:
                        logger.error(f"❌ 使用新 Profile 回答失败 [ID: {question_id}]: {profile_name}")
                        self.record_error(question_name, profile_name)
                except Exception as e:
                    logger.error(f"❌ 使用新 Profile 回答异常 [ID: {question_id}]: {e}")
                finally:
                    with _profile_lock:
                        _profiles_in_use.discard(profile_name)

        # 🔥 所有 profile 都失败后，重置计数器（避免影响下一个问题）
        logger.error(f"❌ 所有 Profile 文件都尝试失败 [ID: {question_id}]: {question_name}")
        raise Exception(f"所有 Profile 都尝试失败，问题：{question_name}")

    def _interact_with_page_core(self, question_id: str, question_name: str, product_llm_task_id: str, round_num: int = 1):
        """核心交互逻辑（使用当前浏览器实例）"""
        self.ask_one_question(question_id, question_name, product_llm_task_id, round_num)

    def _interact_with_page_with_profile(self, question_id: str, question_name: str, product_llm_task_id: str, profile_dir: str, round_num: int = 1):
        """使用指定的 profile 与网页交互
        Args:
            question_id: 问题 ID
            question_name: 问题名称
            product_llm_task_id: 产品任务 ID
            profile_dir: Profile 目录路径
            round_num: 执行轮次（默认 1）
        Returns:
            bool: 是否成功
        """
        try:
            # 启动新的浏览器实例
            old_driver = self.driver
            try:
                self.driver = self.create_driver(profile_dir)
            except Exception as create_error:
                logger.error(f"创建新浏览器实例失败：{create_error}")
                raise

            try:
                # 检查登录状态
                self.ensure_logged_in(self.driver)

                # 使用临时 driver 执行核心交互逻辑
                self._interact_with_page_core(question_id, question_name, product_llm_task_id, round_num)
                return True

            except Exception as e:
                logger.error(f"临时实例执行失败：{e}")
                raise
            finally:
                # 关闭临时 driver，恢复旧 driver
                try:
                    if self.driver:
                        self.driver.quit()
                except:
                    pass
                self.driver = old_driver

        except Exception as e:
            logger.error(f"使用新 Profile 交互失败：{e}")
            import traceback

            logger.error(f"详细错误信息:{traceback.format_exc()}")
            return False

    def ask_one_question(self, question_id: str, question_name: str, product_llm_task_id: str, round_num: int = 1):
        """与网页进行交互的逻辑"""
        wait = WebDriverWait(self.driver, 15)

        time.sleep(random.randint(4, 7))
        new_dialogue = self.driver.find_element(By.CSS_SELECTOR, '[data-testid="create_conversation_button"]')
        new_dialogue.click()
        time.sleep(random.randint(2, 5))

        # 找到输入框
        input_box = wait.until(EC.presence_of_element_located((By.TAG_NAME, "textarea")))
        input_box.click()
        self.driver.execute_script("arguments[0].value = '';", input_box)

        # 逐字输入问题（模拟人工）+ 联网搜索
        question_with_search = f"{question_name} 帮我联网检索最新信息"
        for ch in question_with_search:
            input_box.send_keys(ch)
            time.sleep(random.uniform(0.02, 0.08))
        time.sleep(1)
        input_box.send_keys(Keys.ENTER)

        # 等待回答（40-60 秒）
        time.sleep(random.randint(40, 60))

        # 等待回答内容出现
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, '[data-testid="message_text_content"]')))
        self.doubao_scroll_and_load_refs()
        try:
            answer_divs = self.driver.find_elements(By.CSS_SELECTOR, '[data-testid="message_text_content"]')
            if not answer_divs or len(answer_divs) < 2:
                logger.error(f"未找到足够的回答内容元素，当前数量：{len(answer_divs) if answer_divs else 0}")
                raise Exception("未找到回答内容元素，无法继续处理")
            # 取第二个元素（AI 的回答）
            answer_div = answer_divs[-1]
            logger.info(f"✅ 获取到 {len(answer_divs)} 个回答内容元素，使用第 2 个（AI 回答）")
        except Exception as e:
            logger.error(f"获取回答内容失败：{e}")
            raise

        last_text = ""
        while True:
            visible_text = answer_div.text
            time.sleep(random.randint(5, 8))
            div_value_text = answer_div.text
            if visible_text == div_value_text and len(visible_text) > 0:
                logger.info("✅ 检测到回答完整")
                last_text = visible_text
                break
            else:
                logger.info("❌ 检测到回答中，继续等待...")

        # 存入数据库主表
        reply_content_id = insert_task_question_reply_content(
            question_id=question_id,
            product_llm_task_id=product_llm_task_id,
            llm_search_term="null",
            reply_content=last_text,
            round_num=round_num,
        )

        # 查找并点击网页按钮
        time.sleep(5)
        bu_button = self.driver.find_elements(By.ID, "to-bottom-button")
        if bu_button:
            bu_button[0].click()
            time.sleep(5)
        try:
            web_button = self.driver.find_elements(By.XPATH, '//*[contains(text(), "篇资料")]')
            if web_button:
                web_button[0].click()
                time.sleep(6)

                # 🔥 在主线程中收集 URL 数据
                url_meta_list = self.collect_search_results()
                logger.info(f"✅ 收集到 {len(url_meta_list)} 个 URL")

                # 🔥 在后台线程中处理 URL 请求（不阻塞主进程）
                bg_thread = threading.Thread(
                    target=self._process_urls_in_background,
                    args=(
                        question_id,
                        url_meta_list,
                        product_llm_task_id,
                        reply_content_id,
                        round_num,
                    ),
                    daemon=False,
                )
                bg_thread.start()
                self.background_threads.append(bg_thread)
            else:
                logger.error("未找到 '篇资料' 元素")
                # raise Exception("未找到 '篇资料' 元素，无法获取搜索结果")
        except NoSuchElementException:
            logger.error("未找到 '篇资料' 元素")
            raise Exception("未找到 '篇资料' 元素，无法获取搜索结果")

    def collect_search_results(self):
        """在主线程中收集搜索结果
        Returns:
            url_meta_list: URL 元数据列表 [(href, title, count, site_name), ...]
        """
        try:
            result_divs = self.driver.find_elements(By.CSS_SELECTOR, '[data-testid="search-text-item"]')
            search_count = 0
            url_meta_list = []

            for div in result_divs:
                try:
                    href_value = div.find_element(By.TAG_NAME, "a").get_attribute("href")
                    title_text = ""
                    try:
                        title_element = div.find_element(By.CSS_SELECTOR, ".search-item-title-fLDLZw")
                        title_text = title_element.text
                    except:
                        pass
                    site_name = ""
                    try:
                        site_name_element = div.find_element(By.CSS_SELECTOR,".search-item-footer-b0INFL .footer-title-M1Yzgt", )
                        site_name = site_name_element.text
                    except:
                        pass
                    search_count += 1
                    url_meta_list.append((href_value, title_text, search_count, site_name))
                except Exception as e:
                    logger.error(f"处理搜索结果失败：{e}")
            return url_meta_list
        except Exception as e:
            logger.error(f"收集搜索结果失败：{e}")
            return []

    def _process_urls_in_background(self, question_id: str, url_meta_list: list, product_llm_task_id: str, reply_content_id: str, round_num: int = 1):
        """后台处理 URL 存库和片段关联"""
        try:
            thread_name = threading.current_thread().name
            logger.info(f">>> [{thread_name}] 正在进入后台处理流程...")
            logger.info(f"[{thread_name}] 开始后台处理 {len(url_meta_list)} 个引用")

            search_web_id_map = {}

            # 获取被引用的数字编号
            cited_numbers = set()

            logger.info(f"[{thread_name}] 引用编号：{cited_numbers}")

            # 1. 插入引用 URL 到数据库
            for item in url_meta_list:
                try:
                    href_value, title_text, count, site_name = item
                    is_cite = 1 if count in cited_numbers else 0

                    web_content_id = insert_product_llm_task_web_content(
                        product_llm_task_id=product_llm_task_id,
                        question_id=question_id,
                        web_url=href_value,
                        is_cite=is_cite
                    )

                    if not web_content_id:
                        logger.info(f"⏭ insert_product_llm_task_web_content 未落库，跳过后续处理 [QuestionId={question_id}, SiteUrl={href_value}]")
                        continue

                    insert_task_question_search_web(
                        task_question_search_web_id=web_content_id,
                        question_id=question_id,
                        product_llm_task_id=product_llm_task_id,
                        site_name=site_name,
                        site_title=title_text,
                        site_url=href_value,
                        site_sort=count,
                        is_cite=is_cite,
                        round_num=round_num
                    )

                    if web_content_id:
                        search_web_id_map[count] = web_content_id
                        logger.info(f"[{thread_name}] 📌 建立映射：引用 [{count}] -> WebContentId[{web_content_id}]")

                except Exception as e:
                    logger.error(f"插入引用 URL 失败：{e}")

            # 2. 插入片段（如果有）
            # if reply_content_id and pending_snippets:
            #     for snip in pending_snippets:
            #         content = snip.get('Content', '')
            #         citations = snip.get('Citation', [])
            #         for c_num in citations:
            #             try:
            #                 web_id = search_web_id_map.get(int(c_num))
            #                 if web_id:
            #                     insert_task_question_reply_content_snippet(
            #                         task_question_reply_content_id=reply_content_id,
            #                         task_question_search_web_id=web_id,
            #                         reply_content_snippet=content
            #                     )
            #             except Exception as e:
            #                 logger.error(f"插入片段失败：{e}")

            logger.info(f"[{thread_name}] 后台处理完成")
        except Exception as e:
            logger.error(f"后台线程异常：{traceback.format_exc()}")


def get_questions_from_db() -> List[dict]:
    try:
        conn = pymysql.connect(**DB_CONFIG)
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        cursor.execute("""
                       SELECT llm_task.ProductLlmTaskId,
                              llm_task.ProductTaskId,
                              llm_task.ProductId,
                              llm_task.LlmKey,
                              llm_task.MaxRounds,
                              llm_task.CreatedTime,
                              question.QuestionId,
                              question.QuestionName
                       FROM ent_data_product_llm_task AS llm_task
                                LEFT JOIN ent_data_product_question AS prod_question
                                          ON llm_task.ProductId = prod_question.ProductId
                                              AND prod_question.Deleted = b'0'
                                              AND prod_question.Disabled = b'0'
                                LEFT JOIN ent_data_question AS question
                                          ON prod_question.QuestionId = question.QuestionId
                                              AND question.Deleted = b'0'
                                              AND question.Disabled = b'0'
                       WHERE llm_task.LlmKey = 'doubao'
                         and llm_task.Deleted = b'0'
                         and llm_task.Disabled = b'0'
                         and llm_task.Status = '未开始'
                       ORDER BY llm_task.CreatedTime ASC, prod_question.CreatedTime ASC;
                       """)

        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return rows
    except Exception as e:
        logger.error(f"从数据库获取问题失败：{e}")
        return []


def run_concurrent(max_workers: int = 1, use_proxy: bool = True, executable_path: Optional[str] = None):
    """
    循环并发运行多个账号的任务
    """
    # 扫描可用账号目录
    profile_base = r"D:/doubao_real_profiles"
    if not os.path.exists(profile_base):
        os.makedirs(profile_base)

    account_ids = [d.replace('account_', '') for d in os.listdir(profile_base) if d.startswith('account_')]
    # 🔥 随机打乱账号顺序，避免每次都用同一个账号开头
    random.shuffle(account_ids)
    logger.info(f"🎲 账号顺序已随机打乱：{account_ids}")

    if not account_ids:
        account_ids = ["1"]

    logger.info("🚀 任务监听已启动，每 10 分钟轮询一次数据库...")

    while True:
        try:
            questions = get_questions_from_db()
            if not questions:
                logger.info("😴 暂无待处理任务，10 分钟后再次查询...")
                time.sleep(600)
                continue

            # 去重得到原始问题列表（保持顺序）
            unique_questions = []
            seen_qids = set()
            for q in questions:
                if q['QuestionId'] not in seen_qids:
                    unique_questions.append(q)
                    seen_qids.add(q['QuestionId'])

            max_rounds = unique_questions[0].get('MaxRounds', 1) if unique_questions else 1
            logger.info(f"🔍 原始问题数={len(unique_questions)}，MaxRounds={max_rounds}，开始执行 (账号={account_ids}, 并发数={max_workers})")

            # 按轮次交叉生成任务：(Q1,r1),(Q2,r1),(Q3,r1),(Q1,r2),(Q2,r2),...
            tasks = []
            for round_num in range(1, max_rounds + 1):
                for i, q in enumerate(unique_questions):
                    acc_id = account_ids[(i + (round_num - 1) * len(unique_questions)) % len(account_ids)]
                    tasks.append((acc_id, q, round_num))

            # 🔥 收集所有 ProductLlmTaskId（去重）
            task_ids = list(set(q['ProductLlmTaskId'] for q in unique_questions))

            # 🔥 在线程池执行前，将所有任务状态更新为'进行中'
            for task_id in task_ids:
                try:
                    update_product_llm_task_status(task_id, '进行中')
                    logger.info(f"📝 任务 {task_id} 状态已更新为 '进行中'")
                except Exception as e:
                    logger.error(f"更新任务状态失败：{e}")

            def worker(acc_id, q, round_num):
                doubao = DouBao(account_id=acc_id, use_proxy=use_proxy, executable_path=executable_path)
                doubao.run_single(q, round_num)

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(worker, acc, q, rn) for acc, q, rn in tasks]
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        logger.error(f"Worker 执行出错：{e}")

            # 🔥 所有线程完成后，将所有任务状态更新为'爬网完成'
            for task_id in task_ids:
                try:
                    update_product_llm_task_status(task_id, '爬网完成')
                    logger.info(f"✅ 任务 {task_id} 状态已更新为 '爬网完成'")
                except Exception as e:
                    logger.error(f"更新任务状态失败：{e}")

            logger.info("✅ 本轮任务处理完毕，立即检查是否有新任务...")

        except Exception as e:
            logger.error(f"❌ 运行循环出现异常：{e}")
            time.sleep(10)  # 发生异常时等待再重试，避免死循环刷屏


def _build_question_from_task(task_info: dict) -> dict:
    return {
        "ProductLlmTaskId": task_info.get("product_llm_task_id") or task_info.get("ProductLlmTaskId"),
        "QuestionId": task_info.get("question_id") or task_info.get("QuestionId"),
        "QuestionName": task_info.get("question_name") or task_info.get("QuestionName"),
    }


def _select_account_id(task_info: dict, profile_base: str) -> str:
    account_info = task_info.get("account_info") or {}
    if account_info.get("account_id"):
        return str(account_info["account_id"])

    if os.path.exists(profile_base):
        account_ids = sorted(
            d.replace("account_", "")
            for d in os.listdir(profile_base)
            if d.startswith("account_")
        )
        if account_ids:
            return account_ids[0]

    return "1"


def _allocate_account_for_task(task_info: dict) -> tuple[str, dict, object]:
    account_info = task_info.get("account_info") or {}
    if account_info:
        return _select_account_id(task_info, r"D:/doubao_real_profiles"), account_info, None
    if (task_info.get("dry_run") or os.getenv("CRAWLER_EXECUTE_DRY_RUN") == "1") and not task_info.get("account_allocator"):
        return _select_account_id(task_info, r"D:/doubao_real_profiles"), {}, None
    if AccountAllocator is None:
        return _select_account_id(task_info, r"D:/doubao_real_profiles"), {}, None

    allocator = task_info.get("account_allocator") or AccountAllocator()
    allocated = allocator.allocate("doubao", task_id=task_info.get("task_id"))
    task_info["account_info"] = allocated
    return str(allocated["account_id"]), allocated, allocator


def execute_task(task_info: dict) -> dict:
    """Execute one DouBao question task for the master dispatcher."""
    account_id, account_info, allocator = _allocate_account_for_task(task_info)
    question = _build_question_from_task(task_info)
    round_num = int(task_info.get("round_num") or task_info.get("RoundNum") or 1)

    if task_info.get("dry_run") or os.getenv("CRAWLER_EXECUTE_DRY_RUN") == "1":
        if allocator:
            allocator.release(account_info, success=True, task_id=task_info.get("task_id"))
        return {"success": True, "answer": "", "error": "", "account_id": account_id}

    if webdriver is None:
        raise RuntimeError("selenium is required to execute DouBao tasks")

    try:
        doubao = DouBao(account_id=account_id, use_proxy=task_info.get("use_proxy", True))
        doubao.run_single(question, round_num)
        if allocator:
            allocator.release(account_info, success=True, task_id=task_info.get("task_id"))
        return {"success": True, "answer": "", "error": "", "account_id": account_id}
    except Exception as exc:
        logger.exception("DouBao execute_task failed")
        if allocator:
            allocator.release(account_info, success=False, task_id=task_info.get("task_id"), reason=str(exc))
        return {"success": False, "answer": "", "error": str(exc), "account_id": account_id}


if __name__ == "__main__":
    test_task = {
        "product_llm_task_id": 1,
        "question_id": 1,
        "question_name": "test question",
        "round_num": 1,
        "dry_run": True,
    }
    print(execute_task(test_task))

# & "C:\Program Files\Google\Chrome\Application\chrome.exe" --user-data-dir="D:\doubao_real_profiles\account_1"
