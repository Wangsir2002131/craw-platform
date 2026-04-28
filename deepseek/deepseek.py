# 问题内容，片段，web
import json
import random
import os
import sys
import logging
import pymysql
from typing import Any, Dict, List, Optional, Callable, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from datetime import datetime
import time

try:
    from playwright.sync_api import sync_playwright, Playwright, Page
except ImportError:
    sync_playwright = None
    Playwright = None
    Page = None

try:
    import pymysql
except ImportError:
    pymysql = None

# 添加shared-methods目录到sys.path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), 'shared-methods'))
try:
    from shared_methods import (
        get_loginFile, get_page, wait_load_page,
        get_proxy, dp_api_deepseek, send_dingtalk_message, DB_CONFIG
    )
except Exception:
    DB_CONFIG = {}
    dp_api_deepseek = None

    def get_loginFile(*args, **kwargs):
        return None

    def get_page(*args, **kwargs):
        return None

    def wait_load_page(*args, **kwargs):
        return None

    def get_proxy(*args, **kwargs):
        return None

    def send_dingtalk_message(*args, **kwargs):
        return None

# 添加根目录到sys.path以导入database_usage_example
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
        get_default_product_llm_task_id,
        insert_task_question_search_web,
        update_product_llm_task_status
    )
except Exception:
    def insert_task_question_reply_content(*args, **kwargs):
        return None

    def insert_task_question_reply_content_snippet(*args, **kwargs):
        return None

    def insert_product_llm_task_web_content(*args, **kwargs):
        return None

    def get_default_product_llm_task_id():
        return None

    def insert_task_question_search_web(*args, **kwargs):
        return None

logger = logging.getLogger(__name__)

_cookie_lock = threading.Lock()
_cookies_in_use: set = set()

class DeepSeekDP:

    @staticmethod
    def get_available_cookie_files(current_path: str, folder_name: str, fallback_cookie: Optional[str] = None) -> List[str]:
        cookie_folder_path = os.path.join(current_path, folder_name)
        if os.path.exists(cookie_folder_path):
            cookie_files = [f for f in os.listdir(cookie_folder_path) if f.endswith('.json')]
        else:
            cookie_files = [fallback_cookie] if fallback_cookie else []

        if not cookie_files and fallback_cookie:
            cookie_files = [fallback_cookie]
        return cookie_files or ['cookies1.json']

    @staticmethod
    def normalize_question_task(question_data: Any, default_task_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if isinstance(question_data, dict):
            question_id = question_data.get('QuestionId')
            question_name = question_data.get('QuestionName')
            if not question_id or not question_name:
                return None
            max_rounds = question_data.get('MaxRounds', 1) or 1
            try:
                max_rounds = max(1, int(max_rounds))
            except (TypeError, ValueError):
                max_rounds = 1
            return {
                'QuestionId': question_id,
                'QuestionName': question_name,
                'ProductLlmTaskId': question_data.get('ProductLlmTaskId') or default_task_id,
                'ProductTaskId': question_data.get('ProductTaskId'),
                'TaskName': question_data.get('TaskName'),
                'MaxRounds': max_rounds,
                'RoundNum': question_data.get('RoundNum', 1) or 1,
            }

        if isinstance(question_data, tuple):
            if len(question_data) == 6:
                question_id, question_name, product_llm_task_id, task_name, product_task_id, round_num = question_data
                return {
                    'QuestionId': question_id,
                    'QuestionName': question_name,
                    'ProductLlmTaskId': product_llm_task_id or default_task_id,
                    'ProductTaskId': product_task_id,
                    'TaskName': task_name,
                    'MaxRounds': max(1, int(round_num or 1)),
                    'RoundNum': round_num or 1,
                }
            if len(question_data) == 5:
                question_id, question_name, product_llm_task_id, task_name, product_task_id = question_data
                return {
                    'QuestionId': question_id,
                    'QuestionName': question_name,
                    'ProductLlmTaskId': product_llm_task_id or default_task_id,
                    'ProductTaskId': product_task_id,
                    'TaskName': task_name,
                    'MaxRounds': 1,
                    'RoundNum': 1,
                }
            if len(question_data) == 2:
                question_id, question_name = question_data
                return {
                    'QuestionId': question_id,
                    'QuestionName': question_name,
                    'ProductLlmTaskId': default_task_id,
                    'ProductTaskId': None,
                    'TaskName': None,
                    'MaxRounds': 1,
                    'RoundNum': 1,
                }
        return None

    @classmethod
    def build_round_interleaved_tasks(cls, question_rows: List[Dict[str, Any]],cookie_files: List[str]) -> List[Tuple[str, Dict[str, Any], int]]:
        normalized_questions = []
        seen_keys = set()
        for row in question_rows:
            normalized = cls.normalize_question_task(row)
            if not normalized:
                continue
            dedupe_key = (normalized.get('ProductLlmTaskId'), normalized['QuestionId'])
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            normalized_questions.append(normalized)

        if not normalized_questions:
            return []

        max_rounds = max(question.get('MaxRounds', 1) for question in normalized_questions)
        tasks = []
        for round_num in range(1, max_rounds + 1):
            for question in normalized_questions:
                if round_num > question.get('MaxRounds', 1):
                    continue
                cookie_file = cookie_files[len(tasks) % len(cookie_files)] if cookie_files else 'cookies1.json'
                tasks.append((cookie_file, question, round_num))
        return tasks

    def __init__(self, cookie_file=None, proxy_api: Optional[Callable] = None):
        """
        初始化DeepSeek实例
        Args:
            cookie_file: cookie文件名
            proxy_api: 代理API调用函数, 返回格式如 {'server': 'http://ip:port'}
                      或 {'server': 'http://ip:port', 'username': 'user', 'password': 'pass'}
        """
        self.browser = None
        self.page = None
        self.context = None
        self.conversation_count = 0
        self.cookie_file = cookie_file
        self.proxy_api = proxy_api
        self.proxy = None  # 实际使用的代理配置
        self.currentPath = os.path.dirname(os.path.realpath(__file__))
        self.lock = threading.Lock()  # 线程锁, 用于保护共享资源
        self.product_llm_task_id = get_default_product_llm_task_id()  # 默认ProductLlmTaskId
        self.search_web_id_map = {}  # 引用编号到SearchWebId的映射
        self.total_errors = 0  # 错误计数器
        self.total_errors_num = 0  # 错误计数器
        self.current_round = 1  # 默认轮次为1

    def get_loginFile(self, cookie_file):
        """获取 cookie 文件路径"""
        return get_loginFile(self.currentPath, 'deepseek_cookie_file', cookie_file)

    def get_proxy(self):
        """获取代理配置 (如果配置了代理API则调用)"""
        if self.proxy_api:
            try:
                proxy = self.proxy_api()
                logger.info(f"✓ 获取到代理: {proxy.get('server', 'unknown') if proxy else 'None'}")
                return proxy
            except Exception as e:
                logger.error(f"✗ 获取代理失败: {e}")
                return None
        # 如果没有配置代理API, 使用共享方法中的固定代理
        try:
            proxy = get_proxy()
            logger.info(f"✓ 获取到固定代理: {proxy.get('server', 'unknown') if proxy else 'None'}")
            return proxy
        except Exception as e:
            logger.error(f"✗ 获取固定代理失败: {e}")
            return None

    def get_page_wrapper(self, playwright: Playwright, cookie_file, executable_path=None):
        """获取浏览器页面 (支持代理)"""
        cookie_path = self.get_loginFile(cookie_file)

        # 如果配置了代理API, 获取代理
        if self.proxy_api and not self.proxy:
            self.proxy = self.get_proxy()

        # 调用公共方法, 传入代理配置
        return get_page(playwright, cookie_path, executable_path, proxy=self.proxy)

    def wait_load_page(self, page: Page, state="load"):
        """等待页面加载"""
        wait_load_page(page, state)

    def login(self, page: Page):
        """登陆"""
        page.goto(r'https://chat.deepseek.com/', wait_until="load", timeout=30000)
        self.wait_load_page(page)
        logger.info("✓ 已导航到 DeepSeek 主页, 等待登陆...")

    def login_and_save_cookies(self):
        """执行登陆并保存 cookies 的完整流程"""
        try:
            logger.info(f"开始登陆流程, Cookie 文件: {self.cookie_file}")
            with sync_playwright() as playwright:
                context, page, browser = self.get_page_wrapper(playwright, self.cookie_file)
                self.login(page)
                # 保存 cookie 到文件
                context.storage_state(path=self.get_loginFile(self.cookie_file))
                logger.info(f"✓ Cookie 已保存到 {self.get_loginFile(self.cookie_file)}")
                browser.close()
                return True
        except Exception as e:
            logger.error(f"✗ 登陆流程失败: {e}")
            return False

    def run(self, questions: List[Tuple] = None):
        """主运行函数
        Args:
            questions: 问题列表, 每个元素为 (question_id, question_name, product_llm_task_id, task_name, product_task_id) 元组
                      如果为None, 则从数据库获取问题
        """
        try:
            # 如果没有传入问题列表, 从数据库获取
            manage_task_status = False
            task_ids = []
            if questions is None:
                logger.info("未传入问题列表, 从数据库获取...")
                questions = get_questions_from_db()
                manage_task_status = True
                if not questions:
                    logger.error("从数据库获取问题失败, 程序终止")
                    return

            normalized_questions = []
            for question in questions:
                normalized = self.normalize_question_task(question, self.product_llm_task_id)
                if normalized:
                    normalized_questions.append(normalized)

            if not normalized_questions:
                logger.error("没有可执行的问题数据, 程序终止")
                return

            if manage_task_status:
                task_ids = sorted({question['ProductLlmTaskId'] for question in normalized_questions if question.get('ProductLlmTaskId')})
                for task_id in task_ids:
                    update_product_llm_task_status(task_id, '进行中')
                    logger.info(f"📝 任务 {task_id} 状态已更新为 '进行中'")

            questions = normalized_questions
            # 如果没有指定 cookie_file, 使用默认值
            if not self.cookie_file:
                self.cookie_file = 'cookies1.json'
            # 检查 cookie 文件是否存在且有效
            cookie_path = self.get_loginFile(self.cookie_file)
            use_cookie_login = False
            try:
                if os.path.exists(cookie_path) and os.path.getsize(cookie_path) > 10:
                    with open(cookie_path, 'r') as f:
                        cookie_data = json.load(f)
                        if cookie_data and isinstance(cookie_data, dict) and len(cookie_data) > 0:
                            use_cookie_login = True
                            logger.info(f"✓ 检测到有效的 cookie 文件: {self.cookie_file}")
            except:
                use_cookie_login = False
            if not use_cookie_login:
                # 如果没有有效的 cookie, 先执行登陆
                logger.info("未检测到有效的 cookie, 先执行登陆...")
                if not self.login_and_save_cookies():
                    logger.error("登陆失败, 程序终止")
                    return
                logger.info("登陆完成, 准备启动浏览器...")
            # 如果配置了代理API, 获取代理
            if self.proxy_api and not self.proxy:
                self.proxy = self.get_proxy()
            # 使用 cookie 文件启动浏览器
            logger.info(f"使用 Cookie 文件启动浏览器: {self.cookie_file}, 代理: {self.proxy.get('server') if self.proxy else '无'}")
            try:
                with sync_playwright() as p:
                    # 启动浏览器时配置代理
                    launch_options = {'headless': False}
                    if self.proxy:
                        launch_options['proxy'] = self.proxy

                    browser = p.chromium.launch(**launch_options)
                    try:
                        context_options = {'storage_state': cookie_path}
                        if self.proxy:
                            context_options['proxy'] = self.proxy

                        self.context = browser.new_context(**context_options)
                        self.page = self.context.new_page()

                        try:
                            self.page.goto('https://chat.deepseek.com/', wait_until='domcontentloaded')
                            self.page.wait_for_timeout(30000)
                            sdsk = self.page.query_selector("div[role='button']:has-text('深度思考')")
                            class_attr = sdsk.get_attribute("class")
                            if "ds-toggle-button--selected" in class_attr:
                                logger.info("深度思考已开启")
                            else:
                                logger.info("深度思考未开启，点击开启")
                                sdsk.click()
                                self.page.wait_for_timeout(3000)
                        except:
                            pass

                        logger.info("✓ 已使用 Cookie 启动浏览器")
                        # 处理问题列表
                        self.process_questions(questions)
                        self.page.wait_for_timeout(3000)

                        if manage_task_status:
                            for task_id in task_ids:
                                update_product_llm_task_status(task_id, '爬网完成')
                                logger.info(f"✅ 任务 {task_id} 状态已更新为 '爬网完成'")

                        # 等待所有后台线程完成
                        if hasattr(self, 'background_threads'):
                            logger.info(f"⏳ 等待 {len(self.background_threads)} 个后台线程完成...")
                            for thread in self.background_threads:
                                thread.join()
                            logger.info("✅ 所有后台线程已完成")
                    finally:
                        try:
                            browser.close()
                        except Exception:
                            pass
                        # 确保实例属性不会指向已关闭的浏览器
                        self.browser = None
                        self.context = None
                        self.page = None
            except Exception as e:
                logger.error(f"浏览器启动失败: {e}")
                raise
        except Exception as e:
            logger.error(f"运行失败: {e}")
            raise
        finally:
            # 如果还有未关闭的浏览器, 尝试关闭
            if self.browser:
                try:
                    self.browser.close()
                except Exception as e:
                    logger.warning(f"关闭浏览器时出错: {e}")
                finally:
                    self.browser = None

    def process_questions(self, questions: List[Any]):
        """处理问题列表
        Args:
            questions: 问题列表, 每个元素为 (question_id, question_name, product_llm_task_id, task_name, product_task_id, round_num) 元组
        """
        try:
            if not questions:
                logger.warning("问题列表为空, 跳过处理。")
                return

            for idx, question_data in enumerate(questions, start=1):
                normalized = self.normalize_question_task(question_data, self.product_llm_task_id)
                if not normalized:
                    logger.error(f"问题数据格式错误: {question_data}")
                    continue

                question_id = normalized['QuestionId']
                question_name = normalized['QuestionName']
                self.product_llm_task_id = normalized.get('ProductLlmTaskId') or self.product_llm_task_id
                self.current_round = normalized.get('RoundNum', 1) or 1
                task_name = normalized.get('TaskName')

                logger.info(f"问题 {idx} [ID: {question_id}]: {question_name}")
                if task_name:
                    logger.info(f"任务: {task_name}, ProductLlmTaskId: {self.product_llm_task_id}, 轮次: {self.current_round}")
                self.ask_one_question_with_retry(question_id, question_name)

        except Exception as e:
            logger.error(f"处理问题列表失败: {e}")
            raise

    def run_single(self, question: Any, round_num: int = 1):
        normalized = self.normalize_question_task(question, self.product_llm_task_id)
        if not normalized:
            raise ValueError(f"问题数据格式错误: {question}")

        normalized['RoundNum'] = round_num
        current_cookie = self.cookie_file or 'cookies1.json'
        with _cookie_lock:
            _cookies_in_use.add(current_cookie)

        try:
            self.run(questions=[normalized])
        finally:
            with _cookie_lock:
                _cookies_in_use.discard(current_cookie)

    def ask_one_question_with_retry(self, question_id: str, question_name: str):
        """与网页进行交互的逻辑（包含重试和 Cookie 切换）"""
        all_cookie_files = self.get_available_cookie_files(self.currentPath, "deepseek_cookie_file", self.cookie_file)

        cookie_files_to_try = []
        current_cookie = self.cookie_file or 'cookies1.json'
        if current_cookie in all_cookie_files:
            cookie_files_to_try.append(current_cookie)
        cookie_files_to_try.extend([f for f in all_cookie_files if f != current_cookie])

        for idx, cookie_to_try in enumerate(cookie_files_to_try):
            logger.info(f"尝试使用 Cookie 文件 [{idx + 1}/{len(cookie_files_to_try)}]: {cookie_to_try}")
            if idx == 0:
                try:
                    self._ask_one_question_core(question_id, question_name)
                    logger.info(f"✅ 问题回答成功 [ID: {question_id}] 使用 Cookie: {cookie_to_try}")
                    self.total_errors = 0
                    self.total_errors_num = 0
                    return
                except Exception as e:
                    logger.error(f"❌ 使用当前浏览器回答失败 [ID: {question_id}]: {e}")
            else:
                with _cookie_lock:
                    if cookie_to_try in _cookies_in_use:
                        logger.info(f"⏭️ Cookie [{cookie_to_try}] 正被其他任务占用，跳过")
                        continue
                    _cookies_in_use.add(cookie_to_try)
                try:
                    logger.info(f"🔄 切换到新 Cookie 重试: {cookie_to_try}")
                    success = self._ask_with_new_cookie(question_id, question_name, cookie_to_try)
                    if success:
                        logger.info(f"✅ 问题回答成功 [ID: {question_id}] 使用新 Cookie: {cookie_to_try}")
                        self.cookie_file = cookie_to_try
                        self.total_errors = 0
                        self.total_errors_num = 0
                        return
                    logger.error(f"❌ 使用新 Cookie 回答失败 [ID: {question_id}]: {cookie_to_try}")
                except Exception as e:
                    logger.error(f"❌ 使用新 Cookie 回答异常 [ID: {question_id}]: {e}")
                finally:
                    with _cookie_lock:
                        _cookies_in_use.discard(cookie_to_try)

        logger.error(f"❌ 所有 Cookie 文件都尝试失败 [ID: {question_id}]: {question_name}")
        logger.info("🔄 重置错误计数器，准备处理下一个问题")
        self.total_errors = 0
        self.total_errors_num = 0
        raise Exception(f"所有 Cookie 都尝试失败，问题：{question_name}")

    def _ask_one_question_core(self, question_id: str, question_name: str):
        self._interact_with_page_core(question_id, question_name)

    def _interact_with_page_core(self, question_id: str, question_name: str):
        """核心交互逻辑"""
        try:
            self.page.evaluate("""
                    () => {
                        window.__rawNodes = [];

                        const observer = new MutationObserver((mutations) => {
                            for (const mutation of mutations) {
                                for (const node of mutation.addedNodes) {
                                    if (node.nodeType !== 1) continue;

                                    const text = (node.innerText || '').trim();
                                    if (!text) continue;

                                    window.__rawNodes.push({
                                        text,
                                        tag: node.tagName,
                                        className: node.className,
                                        ts: Date.now()
                                    });

                                    console.log('🧩 新增节点:', text.slice(0, 200));
                                }
                            }
                        });

                        observer.observe(document.body, {
                            childList: true,
                            subtree: true
                        });

                        console.log('✅ 原始 DOM 监听已启动');
                    }
                    """)
            logger.info("注入js, 启动原始 DOM 监听...")
            # 找到输入框
            input_box = self.page.query_selector('textarea[placeholder*="给 DeepSeek 发送消息"]')

            if input_box:
                # 输入问题
                input_box.fill(f'{question_name} 帮我联网检索最新信息')
                self.page.wait_for_timeout(1000)

                # 找到提交按钮 (最后一个 ds-icon 类元素)
                submit_buttons = self.page.query_selector_all('.ds-icon')

                if submit_buttons:
                    # 点击最后一个按钮
                    last_button = submit_buttons[-1]
                    last_button.click()
                    logger.info(f"提交问题 [ID: {question_id}]: {question_name}")

                    # # 等待回答 (30-50秒)
                    self.page.wait_for_timeout(random.randint(40000, 60000))
                    # 等待回答 (60-80秒)
                    # self.page.wait_for_timeout(random.randint(60000, 80000))

                    # 检查回答是否完整
                    div_value = self.page.query_selector_all('.ds-markdown')
                    if div_value:
                        while True:
                            # 在循环内部重新获取最新的元素和文本
                            visible_text = div_value[-1].inner_text()
                            self.page.wait_for_timeout(random.randint(5000, 10000))
                            div_value_text = div_value[-1].inner_text()
                            if visible_text == div_value_text:
                                logger.info('✅ 检测到回答完整')
                                raw = self.page.evaluate("window.__rawNodes") or []
                                reply_content_saved = False
                                for i, r in enumerate(raw[:20]):
                                    logger.info(f'[{i}], {r["text"]}')
                                    search_term = r["text"]
                                    if question_name in search_term:
                                        continue
                                    if ('site:' in search_term or '搜索' in search_term) and len(search_term) >= 30:
                                        self.current_reply_content_id = insert_task_question_reply_content(
                                            question_id=question_id,
                                            product_llm_task_id=self.product_llm_task_id,
                                            llm_search_term=search_term,
                                            reply_content=visible_text,
                                            round_num=self.current_round
                                        )
                                        reply_content_saved = True
                                        break

                                if not reply_content_saved:
                                    self.current_reply_content_id = insert_task_question_reply_content(
                                        question_id=question_id,
                                        product_llm_task_id=self.product_llm_task_id,
                                        llm_search_term='null',
                                        reply_content=visible_text,
                                        round_num=self.current_round
                                    )
                                break
                            else:
                                logger.info('检测到回答不完整, 继续等待...')
                                self.page.wait_for_timeout(random.randint(10000, 30000))
                    else:
                        logger.error("未找到回答内容的目标元素")
                        raise Exception("未找到回答内容的目标元素，无法继续处理")
                    # 获取答案内容
                    target_div = self.page.query_selector_all('.ds-markdown')
                    visible_text = target_div[-1].inner_text()
                    # 🔥 使用线程池执行片段解析(避免事件循环冲突)
                    executor = ThreadPoolExecutor(max_workers=1)
                    future = executor.submit(dp_api_deepseek, visible_text)
                    self.pending_snippets = future.result()  # 阻塞等待结果
                    executor.shutdown(wait=False)
                    logger.info(f"✅ 片段解析完成, 共 {len(self.pending_snippets) if self.pending_snippets else 0} 个片段")
                    logger.info("-" * 80)

                    # 查找并点击网页按钮
                    web_button = self.page.query_selector_all('text=网页')
                    if web_button:
                        web_button[-1].click()
                        self.page.wait_for_timeout(5000)

                        # 🔥 关键修改: 收集URL后立即返回主进程
                        result_divs = self.page.query_selector_all('._223dd7b')
                        url_meta_list = []
                        search_count = 0

                        for div in result_divs:
                            a_tags = div.query_selector_all('a')
                            for a in a_tags:
                                try:
                                    href_value = a.get_attribute('href')
                                    span_tags = a.query_selector_all('span')
                                    site_name = span_tags[0].text_content().strip() if len(span_tags) >= 1 else ""
                                    publish_time_text = span_tags[1].text_content().strip() if len(span_tags) >= 2 else ""
                                    if len(publish_time_text) < 5:
                                        publish_time = datetime(1970, 1, 1)
                                    else:
                                        try:
                                            publish_time = datetime.strptime(publish_time_text, "%Y/%m/%d")
                                        except ValueError:
                                            publish_time = datetime(1970, 1, 1)
                                    title_element = a.query_selector('.search-view-card__title')
                                    title_text = ""
                                    if title_element:
                                        title_text = title_element.text_content()

                                    combined_text = f"{title_text}".strip()
                                    search_count += 1
                                    url_meta_list.append(
                                        (href_value, combined_text, publish_time, search_count, site_name))
                                except Exception as e:
                                    logger.error(f"处理搜索结果失败: {e}")

                        logger.info(f"✅ 收集到 {len(url_meta_list)} 个URL, 已提交到后台线程处理")

                        # 🚀 在完全独立的后台线程中处理 (不阻塞主进程)
                        import threading
                        bg_thread = threading.Thread(
                            target=self._process_urls_in_background,
                            args=(question_id, url_meta_list),
                            daemon=False  # 🔥 改为非守护线程, 确保片段插入完成
                        )
                        bg_thread.start()
                        logger.info("✨ 后台线程已启动, 主进程继续执行...")
                        # 保存线程引用, 方便后续等待 (可选)
                        if not hasattr(self, 'background_threads'):
                            self.background_threads = []
                        self.background_threads.append(bg_thread)
                    else:
                        logger.error("未找到 '网页' 元素")
                else:
                    logger.error("没有找到匹配的元素。")
            else:
                logger.error(f"找不到问题输入框: {question_name}")
                self.total_errors += 1
                logger.warning(f"连续错误次数: {self.total_errors}/3")
                if self.total_errors == 3:
                    self.total_errors = 0
                    self.total_errors_num += 1
                    logger.warning(f"严重错误累计次数: {self.total_errors_num}/3")
                    if self.total_errors_num == 2:
                        logger.error("🚨 连续严重错误达到阈值，发送告警")
                        content_text = f"""平台：deepseek \n
                        账号: {self.cookie_file} \n
                        问题: {question_name} \n
                        错误信息: 找不到问题输入框 \n
                        连续严重错误次数: {self.total_errors_num}
                        """
                        send_dingtalk_message(content_text)
                        self.total_errors = 0
                        self.total_errors_num = 0
        except Exception as e:
            logger.error(f"交互失败: {e}")
            self.total_errors += 1
            logger.warning(f"连续错误次数: {self.total_errors}/3")
            if self.total_errors == 3:
                self.total_errors = 0
                self.total_errors_num += 1
                logger.warning(f"严重错误累计次数: {self.total_errors_num}/3")
                if self.total_errors_num == 2:
                    logger.error("🚨 连续严重错误达到阈值，发送告警")
                    content_text = f"""平台：deepseek \n
                    账号: {self.cookie_file} \n
                    问题: {question_name} \n
                    错误信息:{str(e)} \n
                    连续严重错误次数: {self.total_errors_num}
                    """
                    send_dingtalk_message(content_text)
                    self.total_errors = 0
                    self.total_errors_num = 0
            raise  # 重新抛出异常以便外层处理

    def _ask_with_new_cookie(self, question_id: str, question_name: str, cookie_file: str):
        """使用指定的cookie文件与网页交互
        Args:
            question_id: 问题ID
            question_name: 问题名称
            cookie_file: 要使用的cookie文件名
        Returns:
            bool: 是否成功
        """
        try:
            # 启动新的浏览器实例
            cookie_path = self.get_loginFile(cookie_file)

            # 获取代理配置
            proxy = self.get_proxy() if self.proxy_api else self.proxy

            # 在新线程中执行完整的浏览器操作，避免事件循环冲突
            def run_in_new_thread():
                import threading
                threading.local()

                with sync_playwright() as p:
                    # 启动浏览器时配置代理
                    launch_options = {'headless': False}
                    if proxy:
                        launch_options['proxy'] = proxy

                    browser = p.chromium.launch(**launch_options)
                    try:
                        context_options = {'storage_state': cookie_path}
                        if proxy:
                            context_options['proxy'] = proxy

                        context = browser.new_context(**context_options)
                        page = context.new_page()

                        try:
                            page.goto('https://chat.deepseek.com/', wait_until='domcontentloaded')
                            page.wait_for_timeout(30000)
                            sdsk = page.query_selector("div[role='button']:has-text('深度思考')")
                            class_attr = sdsk.get_attribute("class")
                            if "ds-toggle-button--selected" in class_attr:
                                logger.info("深度思考已开启")
                            else:
                                logger.info("深度思考未开启，点击开启")
                                sdsk.click()
                                page.wait_for_timeout(3000)
                        except:
                            pass

                        logger.info(f"✓ 已使用新 Cookie 启动浏览器: {cookie_file}")

                        # 创建临时实例执行交互
                        temp_instance = DeepSeekDP(cookie_file=cookie_file, proxy_api=self.proxy_api)
                        temp_instance.product_llm_task_id = self.product_llm_task_id
                        temp_instance.current_round = getattr(self, 'current_round', 1)  # 传递当前轮次，如果没有则默认为1

                        # 使用临时实例执行核心交互逻辑
                        temp_instance.page = page
                        temp_instance.context = context
                        temp_instance.browser = browser

                        # 传递当前实例的错误计数器状态给临时实例
                        temp_instance.total_errors = self.total_errors
                        temp_instance.total_errors_num = self.total_errors_num

                        # 执行核心交互逻辑
                        temp_instance._interact_with_page_core(question_id, question_name)

                        # 🔥 成功后：同步临时实例的计数器状态（应该已重置为0）
                        self.total_errors = temp_instance.total_errors
                        self.total_errors_num = temp_instance.total_errors_num

                        return True

                    except Exception as e:
                        # 🔥 失败时：同步临时实例的计数器状态
                        logger.error(f"临时实例执行失败: {e}")
                        self.total_errors = temp_instance.total_errors if hasattr(temp_instance,'total_errors') else self.total_errors
                        self.total_errors_num = temp_instance.total_errors_num if hasattr(temp_instance,'total_errors_num') else self.total_errors_num
                        raise
                    finally:
                        try:
                            browser.close()
                        except:
                            pass

            # 在独立线程中执行
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(run_in_new_thread)
                return future.result()

        except Exception as e:
            logger.error(f"使用新 Cookie 交互失败: {e}")
            import traceback
            logger.error(f"详细错误信息:{traceback.format_exc()}")
            return False

    def _process_urls_in_background(self, question_id: str, url_meta_list: list):
        """在后台线程中处理URL (完全独立, 不阻塞主进程)
        Args:
            question_id: 问题ID
            url_meta_list: URL元数据列表
        """
        try:
            thread_name = threading.current_thread().name
            logger.info(f"[{thread_name}] 🔄 开始后台处理 {len(url_meta_list)} 个URL...")

            # 🔥 建立 index 到 UUID 的映射关系
            self.search_web_id_map = {}  # key: index(1,2,3...), value: web_content_id

            # 🔥 收集所有片段中引用的数字
            cited_numbers = set()
            if hasattr(self, 'pending_snippets') and self.pending_snippets:
                for snippet in self.pending_snippets:
                    citations = snippet.get('Citation', [])
                    for citation in citations:
                        try:
                            cited_numbers.add(int(citation))
                        except:
                            pass
            logger.info(f"[{thread_name}] 📌 片段中引用的URL编号: {sorted(cited_numbers)}")

            logger.info(f"[{thread_name}] ✅ 开始按顺序存入数据库...")

            # 按顺序处理并插入数据库（不获取网页内容）
            for idx, (href_value, combined_text, publish_time, count, site_name) in enumerate(url_meta_list):
                try:
                    logger.info(f"[{thread_name}][{idx + 1}/{len(url_meta_list)}] {combined_text}")
                    logger.info(f"[{thread_name}] 链接地址: {href_value}")
                    logger.info(f"[{thread_name}] 网站名称: {site_name}")

                    # 插入网站HTML内容到表4 (问题网站内容表)不传content字段
                    # 🔥 根据count是否在cited_numbers中来决定is_cite
                    is_cite = 1 if count in cited_numbers else 0
                    logger.info(f"[{thread_name}] 💾 准备插入数据库 (引用编号:{count}, IsCite={is_cite})...")
                    web_content_id = insert_product_llm_task_web_content(
                        product_llm_task_id=self.product_llm_task_id,
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
                        product_llm_task_id=self.product_llm_task_id,
                        site_name=site_name,
                        site_title=combined_text,
                        publish_time=publish_time,
                        site_url=href_value,
                        site_sort=count,
                        is_cite=is_cite,
                        round_num=self.current_round
                    )
                    # 🔥 建立 index 到 web_content_id 的映射 (index从1开始)
                    if web_content_id:
                        self.search_web_id_map[count] = web_content_id  # count即是1,2,3...
                        logger.info(f"[{thread_name}] 📌 建立映射: 引用[{count}] -> WebContentId[{web_content_id}]")
                    else:
                        logger.error(f"[{thread_name}] ❌ 插入数据库失败，返回None")
                except Exception as e:
                    logger.error(f"[{thread_name}] ❌ 错误信息：{e}, 失败链接: {href_value}")
                    import traceback
                    logger.error(f"[{thread_name}] 堆栈信息:{traceback.format_exc()}")
                    logger.info("-" * 50)

            logger.info(f"[{thread_name}] 📊 本次搜索共找到 {len(url_meta_list)} 个结果")

            # 🔥 处理片段 (pending_snippets 已在主线程中完成解析)
            logger.info(f"[{thread_name}] 🔍 检查属性: pending_snippets={hasattr(self, 'pending_snippets')}, current_reply_content_id={hasattr(self, 'current_reply_content_id')}")
            if hasattr(self, 'pending_snippets') and hasattr(self, 'current_reply_content_id'):
                if self.pending_snippets and self.current_reply_content_id:
                    logger.info(f"[{thread_name}] 📝 开始处理片段, 共 {len(self.pending_snippets)} 个片段")
                    logger.info(f"[{thread_name}] ReplyContentId: {self.current_reply_content_id}")

                    # 🔥 使用引用编号从映射中获取对应的 web_content_id
                    for result in self.pending_snippets:
                        content = result.get('Content', '')
                        citations = result.get('Citation', [])  # 确保是列表
                        logger.info(f"内容: {content}, 引用: {citations}")
                        if len(citations) > 0:
                            for citation in citations:
                                citation_num = int(citation)  # 将引用转换为整数 (1,2,3...)
                                # 🔥 从映射中获取对应的 web_content_id
                                web_content_id = self.search_web_id_map.get(citation_num)
                                if web_content_id:
                                    logger.info(f"[{thread_name}] 🔗 引用[{citation_num}] 对应 WebContentId: {web_content_id}")
                                    insert_task_question_reply_content_snippet(
                                        task_question_reply_content_id=self.current_reply_content_id,
                                        task_question_search_web_id=web_content_id,
                                        reply_content_snippet=content
                                    )
                                else:
                                    logger.warning(f"[{thread_name}] ⚠️ 引用[{citation_num}] 未找到对应的WebContentId，跳过")
                elif not self.current_reply_content_id:
                    logger.error(f"[{thread_name}] ❌ current_reply_content_id 为空, 无法插入片段！")
                elif not self.pending_snippets:
                    logger.info(f"[{thread_name}] ℹ️ 没有待处理的片段")
            # 🔥 注意：这里不再重置计数器，由主流程在成功/失败后统一处理
            logger.info(f"[{thread_name}] 🎉 后台任务处理完成！")

        except Exception as e:
            logger.error(f"[{thread_name}] ❌ 后台处理失败: {e}")


def get_questions_from_db() -> List[Dict[str, Any]]:
    """从数据库获取问题列表
    Returns:
        问题列表, 每个元素为 (question_id, question_name) 元组
    """
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
           WHERE llm_task.LlmKey = 'deepseek'
             AND llm_task.Deleted = b'0'
             AND llm_task.Disabled = b'0'
             AND llm_task.Status = '未开始'
           ORDER BY llm_task.CreatedTime ASC, prod_question.CreatedTime ASC;
        """)
        rows = [row for row in cursor.fetchall() if row.get('QuestionId') and row.get('QuestionName')]
        cursor.close()
        conn.close()
        return rows
    except Exception as e:
        logger.error(f"从数据库获取问题失败: {e}")
        return []


def process_cookie_task(cookie_file: str, question: Dict[str, Any], round_num: int, proxy_api: Optional[Callable] = None):
    """
    处理单个cookie文件的任务 (用于并发执行)
    Args:
        cookie_file: cookie文件名
        questions: 问题列表, 每个元素为 (question_id, question_name, product_llm_task_id, task_name, product_task_id) 元组
        proxy_api: 代理API调用函数
    """
    try:
        logger.info(f"[{cookie_file}] 开始处理问题 [ID: {question['QuestionId']}] 第 {round_num} 轮")
        dp = DeepSeekDP(cookie_file=cookie_file, proxy_api=proxy_api)
        dp.run_single(question, round_num)
        logger.info(f"✓ [{cookie_file}] 完成处理问题 [ID: {question['QuestionId']}] 第 {round_num} 轮")
        return True
    except Exception as e:
        logger.error(f"✗ [{cookie_file}] 处理时出错: {e}")
        return False


def run_concurrent(max_workers: int = 2, proxy_api: Optional[Callable] = None, questions: Optional[List[Dict[str, Any]]] = None):
    """
    并发运行多个cookie文件处理任务 (循环轮询模式)
    Args:
        max_workers: 最大并发数 (默认2)
        proxy_api: 代理API调用函数, 每次调用返回一个新的代理配置
                  返回格式: {'server': 'http://ip:port'} 或
                           {'server': 'http://ip:port', 'username': 'user', 'password': 'pass'}
        questions: 问题列表, 如果为None则从数据库获取
                  每个元素为 (question_id, question_name, product_llm_task_id, task_name, product_task_id) 元组
    说明:
        循环轮询模式: 问题1用cookie1, 问题2用cookie2, ..., 问题31又用cookie1
        这样可以控制每个账号的使用频率, 避免单个账号短时间内请求过多
    """
    deep_seek_dp = DeepSeekDP()
    cookie_files = DeepSeekDP.get_available_cookie_files(deep_seek_dp.currentPath, "deepseek_cookie_file")
    random.shuffle(cookie_files)
    logger.info(f"🎲 Cookie 文件顺序已随机打乱：{cookie_files}")

    try:
        if questions is None:
            logger.info("未传入问题列表, 从数据库获取...")
            questions_list = get_questions_from_db()
        else:
            logger.info(f"使用传入的问题列表, 共 {len(questions)} 个问题")
            questions_list = [DeepSeekDP.normalize_question_task(question) for question in questions]
            questions_list = [question for question in questions_list if question]

        if not questions_list:
            logger.error("数据库中没有有效的问题, 程序终止。")
            return

        tasks = DeepSeekDP.build_round_interleaved_tasks(questions_list, cookie_files)
        if not tasks:
            logger.error("没有生成可执行任务, 程序终止。")
            return

        schedule_preview = ", ".join(
            f"{task['QuestionId']}-r{round_num}" for _, task, round_num in tasks
        )
        logger.info(f"🧭 轮次交叉任务顺序: {schedule_preview}")

        task_ids = sorted({question['ProductLlmTaskId'] for _, question, _ in tasks if question.get('ProductLlmTaskId')})
        for task_id in task_ids:
            try:
                update_product_llm_task_status(task_id, '进行中')
                logger.info(f"📝 任务 {task_id} 状态已更新为 '进行中'")
            except Exception as e:
                logger.error(f"更新任务状态失败：{e}")

        logger.info(f"📊 任务统计: 共 {len(tasks)} 个任务, {len(cookie_files)} 个 cookie 文件, {max_workers} 个并发线程")
        logger.info("💡 执行模式: 按轮次交叉调度问题")

        has_failures = False
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(process_cookie_task, cookie_file, question, round_num, proxy_api):
                    (cookie_file, question, round_num)
                for cookie_file, question, round_num in tasks
            }

            completed = 0
            for future in as_completed(futures):
                cookie_file, question, round_num = futures[future]
                completed += 1
                try:
                    result = future.result()
                    if result:
                        logger.info(f"✅ [{completed}/{len(tasks)}] 问题 {question['QuestionId']} 第 {round_num} 轮 ({cookie_file}) 处理成功")
                    else:
                        has_failures = True
                        logger.error(f"❌ [{completed}/{len(tasks)}] 问题 {question['QuestionId']} 第 {round_num} 轮 ({cookie_file}) 处理失败")
                except Exception as e:
                    has_failures = True
                    logger.error(f"❌ [{completed}/{len(tasks)}] 问题 {question['QuestionId']} 第 {round_num} 轮 ({cookie_file}) 执行异常: {e}")

        if has_failures:
            logger.error("存在失败任务，保留当前任务状态，不更新为 '爬网完成'")
            return

        for task_id in task_ids:
            try:
                update_product_llm_task_status(task_id, '爬网完成')
                logger.info(f"✅ 任务 {task_id} 状态已更新为 '爬网完成'")
            except Exception as e:
                logger.error(f"更新任务状态失败：{e}")

        logger.info("🎉 所有任务处理完成！")

    except Exception as e:
        logger.error(f"分配问题到 cookie 时发生错误: {e}")


def main_sync():
    """单线程模式运行 (保持向后兼容)"""
    deepSeekDP = DeepSeekDP()
    # 从数据库获取问题
    questions = get_questions_from_db()
    deepSeekDP.run(questions=questions)


def _build_question_tuple(task_info: dict) -> Tuple:
    product_llm_task_id = task_info.get("product_llm_task_id") or task_info.get("ProductLlmTaskId")
    question_id = task_info.get("question_id") or task_info.get("QuestionId")
    question_name = task_info.get("question_name") or task_info.get("QuestionName")
    round_num = int(task_info.get("round_num") or task_info.get("RoundNum") or 1)
    return (
        question_id,
        question_name,
        product_llm_task_id,
        f"Task_{product_llm_task_id}",
        task_info.get("product_task_id") or task_info.get("ProductTaskId"),
        round_num,
    )


def _select_cookie_file(task_info: dict) -> str:
    account_info = task_info.get("account_info") or {}
    cookie_path = account_info.get("cookie_file_path") or account_info.get("cookie_file")
    if cookie_path:
        return os.path.basename(cookie_path)
    if account_info.get("account_id"):
        return f"cookies{account_info['account_id']}.json"
    return "cookies1.json"


def _allocate_account_for_task(task_info: dict) -> tuple[str, dict, object]:
    account_info = task_info.get("account_info") or {}
    if account_info:
        return _select_cookie_file(task_info), account_info, None
    if (task_info.get("dry_run") or os.getenv("CRAWLER_EXECUTE_DRY_RUN") == "1") and not task_info.get("account_allocator"):
        return _select_cookie_file(task_info), {}, None
    if AccountAllocator is None:
        return _select_cookie_file(task_info), {}, None

    allocator = task_info.get("account_allocator") or AccountAllocator()
    allocated = allocator.allocate("deepseek", task_id=task_info.get("task_id"))
    task_info["account_info"] = allocated
    return _select_cookie_file(task_info), allocated, allocator


def execute_task(task_info: dict, account_info: dict = None) -> dict:
    """Execute one DeepSeek question task for the master dispatcher."""
    if account_info:
        task_info = {**task_info, "account_info": account_info}

    cookie_file, allocated_account, allocator = _allocate_account_for_task(task_info)
    selected_account = allocated_account.get("account_id") or (task_info.get("account_info") or {}).get("account_id")

    if task_info.get("dry_run") or os.getenv("CRAWLER_EXECUTE_DRY_RUN") == "1":
        if allocator:
            allocator.release(allocated_account, success=True, task_id=task_info.get("task_id"))
        return {"success": True, "answer": "", "error": "", "account_id": selected_account or cookie_file}

    if sync_playwright is None:
        raise RuntimeError("playwright is required to execute DeepSeek tasks")

    try:
        dp = DeepSeekDP(cookie_file=cookie_file)
        dp.run(questions=[_build_question_tuple(task_info)])
        if allocator:
            allocator.release(allocated_account, success=True, task_id=task_info.get("task_id"))
        return {"success": True, "answer": "", "error": "", "account_id": selected_account or cookie_file}
    except Exception as exc:
        logger.exception("DeepSeek execute_task failed")
        if allocator:
            allocator.release(allocated_account, success=False, task_id=task_info.get("task_id"), reason=str(exc))
        return {"success": False, "answer": "", "error": str(exc), "account_id": selected_account or cookie_file}


if __name__ == '__main__':
    test_task = {
        "product_llm_task_id": 1,
        "question_id": 1,
        "question_name": "test question",
        "round_num": 1,
        "dry_run": True,
    }
    print(execute_task(test_task))
