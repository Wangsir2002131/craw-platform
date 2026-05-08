import datetime
import json
import os
import re
import time
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
import pymysql
from playwright.sync_api import Playwright, Page
from curl_cffi import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Optional
from contextlib import contextmanager
import requests as req
import threading
import random

# ==================== 全局日志配置 ====================
# 统一日志配置函数
def setup_global_logger():
    """配置全局日志，所有模块共用一个日志文件"""
    # 获取根logger
    root_logger = logging.getLogger()

    # 如果已经配置过，直接返回
    if root_logger.handlers:
        return

    root_logger.setLevel(logging.INFO)

    # 统一的日志格式
    formatter = logging.Formatter(
        '%(asctime)s - %(threadName)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 文件处理器 - 所有日志输出到同一个文件
    file_handler = logging.FileHandler('model_all.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

# 初始化全局日志配置
setup_global_logger()

logger = logging.getLogger(__name__)

# ==================== 数据库连接配置 ====================
# 数据库连接参数
# DB_CONFIG = {
#     'host': 'jumpserver.zhiyunyilu.com',
#     'port': 33061,
#     'user': '1b3b8af5-a7a2-4293-b765-bafa4cf8db9e',
#     'password': '7GHDLEyZsn4ANouV',
#     'db': 'dev-geo',
#     'charset': 'utf8mb4',
#     'cursorclass': pymysql.cursors.DictCursor
# }
DB_CONFIG = {
    'host': '127.0.0.1',
    'port': 3306,
    'user': 'root',
    'password': 'root',
    'db': 'geo',
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}
class DatabaseManager:
    """数据库连接管理类（单例模式，线程安全）"""
    _instance = None
    _local = threading.local()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DatabaseManager, cls).__new__(cls)
        return cls._instance

    def get_connection(self):
        """获取当前线程的数据库连接"""
        if not hasattr(self._local, 'connection') or self._local.connection is None or not self._local.connection.open:
            logger.info(f"线程 {threading.current_thread().name} 创建新的数据库连接...")
            self._local.connection = pymysql.connect(**DB_CONFIG)
            logger.info(f"✓ 线程 {threading.current_thread().name} 数据库连接成功")
        else:
            try:
                self._local.connection.ping(reconnect=True)
            except:
                self._local.connection = pymysql.connect(**DB_CONFIG)
        return self._local.connection

    def close(self):
        """关闭当前线程的数据库连接"""
        if hasattr(self._local, 'connection') and self._local.connection and self._local.connection.open:
            self._local.connection.close()
            logger.info(f"线程 {threading.current_thread().name} 数据库连接已关闭")
            self._local.connection = None

    @contextmanager
    def get_cursor(self, cursor_type=None):
        """获取游标的上下文管理器"""
        conn = self.get_connection()
        cursor = conn.cursor(cursor_type) if cursor_type else conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"数据库操作失败: {e}")
            raise
        finally:
            cursor.close()

# 创建全局数据库管理实例
db_manager = DatabaseManager()

def get_db_connection():
    """获取数据库连接的便捷函数"""
    return db_manager.get_connection()

proxy_server = "tun-uzqqwl.qg.net:12079"
authKey = "E370E7DC"
password = "B4F9586DACF3"

def get_proxy():
    return {
        "server": f"http://{proxy_server}",
        "username": authKey,
        "password": password,
    }

def get_loginFile(currentPath: str, cookie_folder: str, cookie_file: str) -> str:
    """获取 cookie 文件路径
    Args:
        currentPath: 当前文件所在目录
        cookie_folder: cookie文件夹名称（如 'kimi_cookie_file'）
        cookie_file: cookie文件名
    Returns:
        完整的cookie文件路径
    """
    loginJsonFile = os.path.join(currentPath, f"{cookie_folder}/{cookie_file}")
    if not os.path.exists(loginJsonFile):
        os.makedirs(os.path.dirname(loginJsonFile), exist_ok=True)
        with open(loginJsonFile, 'w') as json_file:
            json_file.write('{}')
    return loginJsonFile

def get_page(playwright: Playwright, cookie_path: str, executable_path=None, proxy: Optional[Dict] = None, user_data_dir: Optional[str] = None):
    """获取浏览器页面
    Args:
        playwright: Playwright实例
        cookie_path: cookie文件完整路径
        executable_path: 浏览器可执行文件路径（可选）
        proxy: 代理配置（可选），格式如 {'server': 'http://ip:port'} 
               或 {'server': 'http://ip:port', 'username': 'user', 'password': 'pass'}
        user_data_dir: 浏览器用户数据目录（可选），用于持久化登录
    Returns:
        (context, page, browser) 元组
    """
    # 注意：代理需要同时在 launch 和 new_context 中配置，并启用 ignore_https_errors
    launch_args = [
        '--no-sandbox', "--start-maximized",
        '--disable-web-security',
        '--allow-insecure-localhost',
        '--allow-running-insecure-content',
        '--ignore-certificate-errors',
        '--disable-download-notification',
        '--safebrowsing-disable-download-protection',
        '--disable-blink-features=AutomationControlled',  # 隐藏自动化控制提示
        '--exclude-switches=enable-automation',  # 移除自动化标识
        '--disable-infobars'  # 禁用信息栏
    ]
    
    # 如果指定了 user_data_dir，则使用持久化模式
    if user_data_dir:
        browser = playwright.chromium.launch_persistent_context(
            user_data_dir,
            headless=False,
            executable_path=executable_path if executable_path else None,
            proxy=proxy if proxy else None,
            args=launch_args,
            ignore_default_args=["--enable-automation"],  # 彻底移除自动化标识
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
            accept_downloads=True,
            bypass_csp=True,
            ignore_https_errors=True,
            chromium_sandbox=False,  # 禁用沙盒
            extra_http_headers={
                "Content-Disposition": "attachment"
            },
        )
        page = browser.pages[0] if browser.pages else browser.new_page()
        
        # 通过 JavaScript 移除自动化检测标识
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        
        return browser, page, browser  # 持久化模式下 context 就是 browser
    
    # 原有的无痕模式逻辑
    browser = playwright.chromium.launch(
        headless=False,
        executable_path=executable_path if executable_path else None,
        proxy=proxy if proxy else None,
        args=launch_args
    )

    try:
        context = browser.new_context(
            accept_downloads=True,
            bypass_csp=True,
            ignore_https_errors=True,  # 关键：使用代理时必须忽略HTTPS错误
            extra_http_headers={
                "Content-Disposition": "attachment"
            },
            storage_state=cookie_path,
        )
    except:
        context = browser.new_context(
            accept_downloads=True,
            bypass_csp=True,
            ignore_https_errors=True,  # 关键：使用代理时必须忽略HTTPS错误
            extra_http_headers={
                "Content-Disposition": "attachment"
            },
        )
    page = context.new_page()
    return context, page, browser

def get_page_1(playwright: Playwright, cookie_path: str, executable_path=None, proxy: Optional[Dict] = None):
    """获取浏览器页面
    Args:
        playwright: Playwright实例
        cookie_path: cookie文件完整路径
        executable_path: 浏览器可执行文件路径（可选）
        proxy: 代理配置（可选），格式如 {'server': 'http://ip:port'}
               或 {'server': 'http://ip:port', 'username': 'user', 'password': 'pass'}
    Returns:
        (context, page, browser) 元组
    """
    # 注意：代理需要同时在 launch 和 new_context 中配置，并启用 ignore_https_errors
    browser = playwright.chromium.launch(
        headless=False,
        executable_path=executable_path if executable_path else None,
        proxy=proxy if proxy else None,
        args=[
            '--no-sandbox', "--start-maximized",
            '--disable-web-security',
            '--allow-insecure-localhost',
            '--allow-running-insecure-content',
            '--ignore-certificate-errors',
            '--disable-download-notification',
            '--safebrowsing-disable-download-protection'
        ]
    )

    try:
        context = browser.new_context(
            accept_downloads=True,
            bypass_csp=True,
            ignore_https_errors=True,  # 关键：使用代理时必须忽略HTTPS错误
            extra_http_headers={
                "Content-Disposition": "attachment"
            },
            storage_state=cookie_path,
        )
    except:
        context = browser.new_context(
            accept_downloads=True,
            bypass_csp=True,
            ignore_https_errors=True,  # 关键：使用代理时必须忽略HTTPS错误
            extra_http_headers={
                "Content-Disposition": "attachment"
            },
        )
    page = context.new_page()
    return context, page, browser

def wait_load_page(page: Page, state="load"):
    """等待页面加载
    Args:
        page: 页面对象
        state: 加载状态
    """
    while True:
        try:
            page.wait_for_timeout(2000)
            page.wait_for_load_state(state)
            page.wait_for_timeout(1000)
            break
        except:
            continue

def segment_article_with_citations(article_text: str) -> List[Dict[str, str]]:
    """从文本中提取正文和引用
    参数:
        article_text: 输入文本
    返回:
        List[Dict]: 包含text和quote字段的字典列表
    """
    # 初始化结果列表
    result = []

    # 使用正则表达式分割文本
    # 匹配模式：-后跟数字（可以是一个或多个数字，可能有多个数字范围）
    pattern = r'(-\d+(?:-\d+)*)'

    # 找到所有匹配的位置
    positions = []
    for match in re.finditer(pattern, article_text):
        positions.append(match.start())

    if not positions:
        # 如果没有引用，返回整个文本
        return [{"text": article_text.strip(), "quote": ""}]

    # 处理文本
    current_pos = 0

    for i, pos in enumerate(positions):
        # 获取当前段落的文本
        segment_text = article_text[current_pos:pos].strip()

        if segment_text:
            # 前一个段落没有引用
            result.append({"text": segment_text, "quote": ""})

        # 找到引用结束的位置
        match = re.match(r'-\d+(?:-\d+)*', article_text[pos:])
        if match:
            quote_text = match.group(0)
            result.append({"text": "", "quote": quote_text})
            current_pos = pos + len(quote_text)

    # 处理最后一段文本
    if current_pos < len(article_text):
        last_segment = article_text[current_pos:].strip()
        if last_segment:
            result.append({"text": last_segment, "quote": ""})
    # 清理和合并结果
    cleaned_result = []
    for item in result:
        if not item["text"] and not item["quote"]:
            continue
        # 如果当前条目只有引用，且前一个条目有文本，则将引用合并到前一个条目
        if item["text"] == "" and item["quote"] != "" and cleaned_result:
            cleaned_result[-1]["quote"] = item["quote"]
        else:
            cleaned_result.append(item)

    return cleaned_result

def detail_url(href: str, max_retries: int = 3) -> Dict[str, str]:
    """同步版本 - 保持兼容性"""
    return _detail_url_impl(href, max_retries)

def _detail_url_impl(href: str, max_retries: int = 3) -> Dict[str, str]:
    """提取网页body内的完整内容，保留更多有用信息
    Args:
        href: 网页URL
        max_retries: 最大重试次数
    Returns:
        包含title和content的字典，或包含error的字典
    """
    headers = {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'Pragma': 'no-cache',
        'Upgrade-Insecure-Requests': '1',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
    }
    for attempt in range(max_retries):
        try:
            response = requests.get(href, headers=headers, timeout=(10, 15))
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')
            # 只移除确定无关的元素
            for element in soup(['script', 'style', 'iframe', 'noscript']):
                element.decompose()
            # 移除弹窗，但保留其他交互元素
            popup_selectors = ['.popup', '.modal', '.dialog', '.overlay',
                               '#popup', '#modal', '#dialog', '#overlay']
            for selector in popup_selectors:
                for popup in soup.select(selector):
                    popup.decompose()
            body_html = str(soup.body)
            body_inner_text = soup.body.get_text(strip=True)
            # 调用提取函数获取网站元数据
            # websitename, title, author, publishtime = detail_extraction(body_html)
            # 暂时返回占位值，不调用耗时的 API
            websitename = None
            title = None
            author = None
            publishtime = None
            return {
                "websitename": websitename,
                "title": title,
                "author": author,
                "publishtime": publishtime,
                "body_html": body_html,  # 返回HTML内容
                "content": body_inner_text # 返回文本内容内容
            }

        except requests.exceptions.Timeout as e:
            logger.warning(f"detail_url 超时 {attempt + 1}/{max_retries}: {href}")
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                logger.info(f"detail_url 等待{wait_time}秒后重试...")
                time.sleep(wait_time)
            else:
                return {'error': f'detail_url 请求超时: {str(e)}'}
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"detail_url 连接错误 {attempt + 1}/{max_retries}: {href}")
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                logger.info(f"detail_url 等待{wait_time}秒后重试...")
                time.sleep(wait_time)
            else:
                return {'error': f'detail_url 连接失败: {str(e)}'}
        except Exception as e:
            logger.warning(f"detail_url 请求尝试 {attempt + 1}/{max_retries} 失败: {e}")
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                logger.info(f"detail_url 等待{wait_time}秒后重试...")
                time.sleep(wait_time)
            else:
                return {'error': f'detail_url 网络请求失败: {str(e)}'}

    return {'error': 'detail_url 达到最大重试次数，请求失败'}

def split_citation(citation):
    c = str(citation).replace("[", "").replace("]", "").replace(" ", "").replace(",", "")

    result = []
    i = 0
    while i < len(c):
        if i + 1 < len(c) and c[i+1] == "0":
            result.append(int(c[i] + "0"))
            i += 2
        else:
            result.append(int(c[i]))
            i += 1
    return result

def dp_api_deepseek(text, max_retries: int = 3):
    """同步版本 - 保持兼容性"""
    return _dp_api_deepseek_impl(text, max_retries)

def _dp_api_deepseek_impl(text, max_retries: int = 3):
    logger.info("dp_api_deepseek 正在调用deepseek api接口")
    url = "https://assistant-api.zhiyunyilu.com/chat/completions"

    content = f"""
        - 角色: 文本信息分析专家
        - 背景: 用户需从给定文本中提取与特定回答主题相关的段落及其对应的引用序号，并整理出其中提及的具体信息。
        - 核心技能: 
        1.文本关键信息定位与提取；
        2.学术引用格式识别（如数字标注 -X-X、括号标注 [X] 等）；
        3.医疗/科技专业术语理解；
        4.严格遵循原文，避免主观推断；
        - 任务目标：
        1.识别文本中带有引用序号的具体句子、分句或语义片段。
        2.提取该段落对应的所有引用序号。
        3.将提取的内容整理为结构化数据，确保一条引用对应一段特定的文字，不进行段落级合并。
        - 约束条件：
        1.仅处理文本中明确写出的内容，不进行任何推断、补充或解释。
        2.若两个引用主题位置距离很近，分别提取并保持独立输出项。
        3.严禁将同一个段落中位置不同的引用合并输出。
        4.引用序号需完整列出，按原文顺序整理。
        5.不添加原文中没有的描述或总结。
        6.注意markdown格式中的格式干扰。
        - 输出格式JSON格式: [
        {{
        "Content":"提取的完整主题段落原文",
        "Citation":[引用序号列表]
        }}
        ]。
        - 工作流程:
          1. 通读全文，定位所有可能对应回答主题的段落。
          2. 针对每个段落：
            提取直接相关的句子或句群作为 Content。
            识别该部分中所有引用标记（如 -3-6-9 或 [1,2]），转为纯数字数组。
          3. 每个独立主题段落输出为一个 JSON 对象。
          4.如无引用序号，且Citation 数组为 []，则不需要输出。
        - 示例:
          - 输入文本：对于脑膜转移的治疗，除了副作用相对较大的鞘注化疗（腰椎穿刺注射），目前确实有更优化的局部给药技术，以及其他通过静脉给药的系统性治疗
        方案可供考虑。下面的表格整理了主要的几类治疗途径和选择，你可以快速了解。治疗途径核心药物/技术关键特点主要考虑因素局部给药 (绕过血脑屏障)Ommaya囊脑
        室内给药-2-4-6
        -输出：
        {{
        "Content":"对于脑膜转移的治疗，除了副作用相对较大的鞘注化疗（腰椎穿刺注射），目前确实有更优化的局部给药技术，以及其他通过静脉给药的系统性治疗
        方案可供考虑。下面的表格整理了主要的几类治疗途径和选择，你可以快速了解。治疗途径核心药物/技术关键特点主要考虑因素局部给药 (绕过血脑屏障)Ommaya囊脑
        室内给药
        "Citation":[2,4,6]
        }}
        ]
        文本如下：
        {text}
    """
    payload = {
        "modelId": "4",
        "appKey": "458c6df732a9d80dfbad8dc7872e84ec",
        "messages": [
            {
                "role": "user",
                "content": content
            }
        ],
        "stream": False
    }

    headers = {"Content-Type": "application/json"}

    for i in range(max_retries):
        try:
            resp = req.post(url, data=json.dumps(payload), headers=headers)
            if resp.status_code == 200:
                try:
                    data = resp.json()['choices'][0]['message']['content']
                    try:
                        parsed = json.loads(data)
                        results = []
                        for line in parsed:
                            content = line['Content']
                            citation = line['Citation']
                            parts = split_citation(citation)
                            results.append({
                                'Content': content,
                                'Citation': parts
                            })
                        return results
                    except:
                        logger.error(f"dp_api_deepseek 解析json失败，尝试使用正则匹配")
                        start = data.find('[')
                        end = data.rfind(']') + 1
                        json_text = data[start:end]
                        parsed = json.loads(json_text)
                        # results = []
                        # for line in data_dict:
                        #     content = line['Content']
                        #     citation = line['Citation']
                        #     parts = split_citation(citation)
                        #
                        #     results.append({
                        #         'Content': content,
                        #         'Citation': parts
                        #     })
                        return parsed
                except Exception as e:
                    logger.error(f"dp_api_deepseek 解析响应失败: {e}")
                    continue
            logger.error(f"dp_api_deepseek 请求失败，状态码: {resp.status_code}，正在重试 {i + 1}/{max_retries}")
        except Exception as e:
            logger.error(f"dp_api_deepseek 请求失败: {e}")
    logger.error("dp_api_deepseek 多次重试后接口仍未成功")
    return None

def dp_api_yuanbao(text, max_retries: int = 3):
    """同步版本 - 保持兼容性"""
    return _dp_api_yuanbao_impl(text, max_retries)

def _dp_api_yuanbao_impl(text, max_retries: int = 3):
    logger.info("dp_api_yuanbao 正在调用deepseek api接口")
    url = "https://assistant-api.zhiyunyilu.com/chat/completions"

    content = f"""
        - 角色: 文本信息分析专家
        - 背景: 用户需从给定文本中提取与特定回答主题相关的段落及其对应的引用序号，并整理出其中提及的具体信息。
        - 核心技能: 
        1.文本关键信息定位与提取；
        2.学术引用格式识别（如数字标注 -X-X、括号标注 [X] 等）；
        3.医疗/科技专业术语理解；
        4.严格遵循原文，避免主观推断；
        - 任务目标：
        1.识别文本中带有引用序号的具体句子、分句或语义片段。
        2.提取该段落对应的所有引用序号。
        3.将提取的内容整理为结构化数据，确保一条引用对应一段特定的文字，不进行段落级合并。
        - 约束条件：
        1.仅处理文本中明确写出的内容，不进行任何推断、补充或解释。
        2.若两个引用主题位置距离很近，分别提取并保持独立输出项。
        3.严禁将同一个段落中位置不同的引用合并输出。
        4.引用序号需完整列出，按原文顺序整理。
        5.不添加原文中没有的描述或总结。
        6.若Citation输出为[]，则不输出
        7.注意markdown格式中的格式干扰。
        - 输出格式JSON格式: [
        {{
        "Content":"提取的完整主题段落原文",
        "Citation":[引用序号列表]
        }}
        ]。
        - 工作流程:
          1. 通读全文，定位所有可能对应回答主题的段落。
          2. 针对每个段落：
            提取直接相关的句子或句群作为 Content。
            识别该部分中所有引用标记（如 -3-6-9 或 [1,2]），转为纯数字数组。
          3. 每个独立主题段落输出为一个 JSON 对象。
          4.如无引用序号，且Citation 数组为 []，则不需要输出。
        - 示例:
          - 输入文本：对于脑膜转移的治疗，除了副作用相对较大的鞘注化疗（腰椎穿刺注射），目前确实有更优化的局部给药技术，以及其他通过静脉给药的系统性治疗
        方案可供考虑。下面的表格整理了主要的几类治疗途径和选择，你可以快速了解。治疗途径核心药物/技术关键特点主要考虑因素局部给药 (绕过血脑屏障)Ommaya囊脑
        室内给药
        1
        2
        3
        。
        -输出：
        {{
        "Content":"对于脑膜转移的治疗，除了副作用相对较大的鞘注化疗（腰椎穿刺注射），目前确实有更优化的局部给药技术，以及其他通过静脉给药的系统性治疗
        方案可供考虑。下面的表格整理了主要的几类治疗途径和选择，你可以快速了解。治疗途径核心药物/技术关键特点主要考虑因素局部给药 (绕过血脑屏障)Ommaya囊脑
        室内给药
        "Citation":[1,2,3]
        }}
        ]
        文本如下：
        {text}
    """
    payload = {
        "modelId": "4",
        "appKey": "458c6df732a9d80dfbad8dc7872e84ec",
        "messages": [
            {
                "role": "user",
                "content": content
            }
        ],
        "stream": False
    }

    headers = {"Content-Type": "application/json"}
    for i in range(max_retries):
        try:
            resp = req.post(url, data=json.dumps(payload), headers=headers)
            if resp.status_code == 200:
                try:
                    data = resp.json()['choices'][0]['message']['content']
                    try:
                        parsed = json.loads(data)
                        results = []
                        for line in parsed:
                            content = line['Content']
                            citation = line['Citation']
                            parts = split_citation(citation)
                            results.append({
                                'Content': content,
                                'Citation': parts
                            })
                        return results
                    except:
                        logger.error(f"dp_api_yuanbao 解析json失败，尝试使用正则匹配")
                        start = data.find('[')
                        end = data.rfind(']') + 1
                        json_text = data[start:end]
                        parsed = json.loads(json_text)
                        # j = re.search(r'\{[\s\S]*\}', data)
                        # data_dict = json.loads(j.group())
                        # results = []
                        # for line in data_dict:
                        #     content = line['Content']
                        #     citation = line['Citation']
                        #     parts = split_citation(citation)
                        #
                        #     results.append({
                        #         'Content': content,
                        #         'Citation': parts
                        #     })
                        return parsed
                except Exception as e:
                    logger.error(f"dp_api_yuanbao 解析响应失败: {e}")
                    continue

            logger.error(f"dp_api_yuanbao 请求失败，状态码: {resp.status_code}，正在重试 {i + 1}/{max_retries}")
        except Exception as e:
            logger.error(f"错误：{e}")
    logger.error("dp_api_yuanbao 多次重试后接口仍未成功")
    return None

def detail_extraction(body_html, max_retries: int = 3):
    """同步版本 - 保持兼容性"""
    return _detail_extraction_impl(body_html, max_retries)

def _detail_extraction_impl(body_html, max_retries: int = 3):
    logger.info("正在调用deepseek api接口, 解析详情页")
    url = "https://assistant-api.zhiyunyilu.com/chat/completions"
    content = f"""
            - 角色: 网页元数据提取专家
            - 背景: 用户提供一段网页的 HTML 代码（主要为 body 部分），需要从中精准提取出该网页的四个核心属性：网站名称、网页标题、作者、发布时间。
            - 核心技能: 
                1. DOM 结构语义分析（识别 header, footer, article, h1 等标签含义）；
                2. 基于类名（ClassName）和 ID 的特征识别（如识别 .author, .date, .site-logo 等）；
                3. 文本模式识别（从 "By John Doe" 中提取 "John Doe"，从 "© 2023 Baidu" 中提取 "Baidu"）；
                4. HTML 标签清洗（从提取的节点中去除 HTML 标签，仅保留有效文本）。
            - 任务目标：
                1. 提取网站名称 (websitename)：分析导航栏、Logo alt 属性、页脚版权声明（Copyright）或面包屑导航，提取所属网站/机构名称。
                2. 提取网页标题 (title)：定位页面中最显著的标题（通常是 `<h1>` 标签，或文章区域顶部的核心文本）。
                3. 提取作者 (author)：寻找文章头部或尾部的作者信息，识别 "By", "作者", "Editor" 等关键词或相关 CSS 类名。
                4. 提取发布时间 (publishtime)：寻找 `<time>` 标签，或包含日期格式（YYYY-MM-DD, X天前）的文本节点。
                5. 提取出来的发布时间(publishtime)，都变为YYYY-MM-DD 00:00:00 格式，如果没有年份就按照当前年份来，若是只返回了年份，则按照当年的月份和天来，若是只返回了月份和天，则按照当前的年份和天来，例如，不要输出 `2020`，要输出 `2020-12-12 00:00:00`
            - 约束条件：
                1. 数据清洗：提取结果必须是纯文本。例如，不要输出 `<span>张三</span>`，只输出 `张三`。
                2. 去噪：
                   - 作者栏若包含 "By ", "文 / " 等前缀，需去除，只保留人名。
                   - 时间栏若包含 "发布于 ", "Updated " 等前缀，需去除，只保留时间字符串。
                3. 真实性：仅依据提供的 HTML 内容提取。若文中完全未提及某项信息（如无作者），该字段对应的值输出为 `null`（不要输出 "未找到" 或空字符串）。
                4. 优先级：若存在多个时间（发布时间、更新时间），优先提取发布时间。
                5. 输出格式：严格遵守 JSON 格式输出。
            - 输出格式 JSON:
            {{
                "websitename": "提取到的网站名称或 null",
                "title": "提取到的网页标题或 null",
                "author": "提取到的作者或 null",
                "publishtime": "提取到的时间字符串或 null"
            }}
            - 工作流程:
                  1. 全局扫描：快速分析 HTML 结构，区分导航区（Header）、正文区（Main/Article）和页脚（Footer）。
                  2. 定位标题：在正文区顶部寻找 `<h1>` 或字体最大的标题元素。
                  3. 定位元数据区：通常位于标题下方或正文结束处，寻找包含 class="meta", "info", "author", "date" 的节点。
                  4. 定位网站名：若 body 中无明确 meta 数据，检查 Header 中的 Logo 说明或 Footer 中的版权声明。
                  5. 提取与清洗：锁定目标节点，提取 innerText，利用正则或规则去除多余修饰词（如空格、换行、前缀）。
                  6. JSON 生成：将清洗后的数据填入 JSON 模板输出。
            html如下：
            {body_html}
        """
    payload = {
        "modelId": "4",
        "appKey": "458c6df732a9d80dfbad8dc7872e84ec",
        "messages": [
            {
                "role": "user",
                "content": content
            }
        ],
        "stream": False
    }

    headers = {"Content-Type": "application/json"}

    for i in range(max_retries):
        resp = req.post(url, data=json.dumps(payload), headers=headers)
        if resp.status_code == 200:
            try:
                if 'error' in resp.json() and 'message' in resp.json()['error']:
                    logger.error(f"模型调用失败: {resp.json()['error']['message']}")
                    break
                data = resp.json()['choices'][0]['message']['content']
                try:
                    data_dict = json.loads(data)
                    websitename = data_dict['websitename']
                    title = data_dict['title']
                    author = data_dict['author']
                    try:
                        publishtime = data_dict['publishtime']
                    except:
                        publishtime = '1970-01-01 00:00:00'
                    return websitename, title, author, publishtime
                except:
                    logger.error(f"detail_extraction 解析json失败，尝试使用正则匹配")
                    j = re.search(r'\{[\s\S]*\}', data)
                    data_dict = json.loads(j.group())
                    websitename = data_dict['websitename']
                    title = data_dict['title']
                    author = data_dict['author']
                    try:
                        publishtime = data_dict['publishtime']
                    except:
                        publishtime = '1970-01-01 00:00:00'
                    return websitename, title, author, publishtime
            except Exception as e:
                logger.error(f"detail_extraction 解析响应失败: {e}")
                continue

        logger.error(f"detail_extraction 请求失败，状态码: {resp.status_code}，正在重试 {i + 1}/{max_retries}")
    logger.error("detail_extraction 多次重试后接口仍未成功")
    return None

def dp_api_afu(text, max_retries: int = 3):
    """同步版本 - 保持兼容性"""
    return _dp_api_afu_impl(text, max_retries)

def _dp_api_afu_impl(text, max_retries: int = 3):
    logger.info("dp_api_afu 正在调用deepseek api接口")
    url = "https://assistant-api.zhiyunyilu.com/chat/completions"

    content = f"""
        - 角色: 文本信息分析专家
        - 背景: 用户需从给定文本中提取与特定回答主题相关的段落及其对应的引用序号，并整理出其中提及的具体信息。
        - 核心技能: 
        1.文本关键信息定位与提取；
        2.学术引用格式识别（如数字标注 -X-X、括号标注 [X] 等）；
        3.医疗/科技专业术语理解；
        4.严格遵循原文，避免主观推断；
        - 任务目标：
        1.识别文本中带有引用序号的具体句子、分句或语义片段。
        2.提取该段落对应的所有引用序号。
        3.将提取的内容整理为结构化数据，确保一条引用对应一段特定的文字，不进行段落级合并。
        - 约束条件：
        1.仅处理文本中明确写出的内容，不进行任何推断、补充或解释。
        2.若两个引用主题位置距离很近，分别提取并保持独立输出项。
        3.严禁将同一个段落中位置不同的引用合并输出。
        4.引用序号需完整列出，按原文顺序整理。
        5.不添加原文中没有的描述或总结。
        6.注意markdown格式中的格式干扰。
        - 输出格式JSON格式: [
        {{
        "Content":"提取的完整主题段落原文",
        "Citation":[引用序号列表]
        }}
        ]。
        - 工作流程:
          1. 通读全文，定位所有可能对应回答主题的段落。
          2. 针对每个段落：
            提取直接相关的句子或句群作为 Content。
            识别该部分中所有引用标记（如 -3-6-9 或 [3],[6],[9]），转为纯数字数组。
          3. 每个独立主题段落输出为一个 JSON 对象。
          4.如无引用序号，且Citation 数组为 []，则不需要输出。
        - 示例:
          - 输入文本：临床关注点应转向：共病管理：注意评估和管理可能伴随的其他特应性疾病，如过敏性哮喘、过敏性鼻炎或食物过敏[16][17]
        -输出：
        {{
        "Content":"临床关注点应转向：共病管理：注意评估和管理可能伴随的其他特应性疾病，如过敏性哮喘、过敏性鼻炎或食物过敏
        "Citation":[16,17]
        }}
        ]
        文本如下：
        {text}
    """
    payload = {
        "modelId": "4",
        "appKey": "458c6df732a9d80dfbad8dc7872e84ec",
        "messages": [
            {
                "role": "user",
                "content": content
            }
        ],
        "stream": False
    }

    headers = {"Content-Type": "application/json"}

    for i in range(max_retries):
        resp = req.post(url, data=json.dumps(payload), headers=headers)
        if resp.status_code == 200:
            try:
                data = resp.json()['choices'][0]['message']['content']
                try:
                    parsed = json.loads(data)
                    results = []
                    for line in parsed:
                        content = line['Content']
                        citation = line['Citation']
                        parts = split_citation(citation)
                        results.append({
                            'Content': content,
                            'Citation': parts
                        })
                    return results
                except:
                    logger.error(f"dp_api_deepseek 解析json失败，尝试使用正则匹配")
                    start = data.find('[')
                    end = data.rfind(']') + 1
                    json_text = data[start:end]
                    parsed = json.loads(json_text)
                    # results = []
                    # for line in data_dict:
                    #     content = line['Content']
                    #     citation = line['Citation']
                    #     parts = split_citation(citation)
                    #
                    #     results.append({
                    #         'Content': content,
                    #         'Citation': parts
                    #     })
                    return parsed
            except Exception as e:
                logger.error(f"dp_api_deepseek 解析响应失败: {e}")
                continue

        logger.error(f"dp_api_deepseek 请求失败，状态码: {resp.status_code}，正在重试 {i + 1}/{max_retries}")
    logger.error("dp_api_deepseek 多次重试后接口仍未成功")
    return None

# ==================== 异步执行辅助函数 ====================

# 创建全局线程池（用于IO密集型任务）
_thread_pool = ThreadPoolExecutor(max_workers=10)

async def async_detail_url(href: str, max_retries: int = 3) -> Dict[str, str]:
    """异步版本的 detail_url"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_thread_pool, detail_url, href, max_retries)

async def async_dp_api_deepseek(text, max_retries: int = 3):
    """异步版本的 dp_api_deepseek"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_thread_pool, dp_api_deepseek, text, max_retries)

async def async_dp_api_yuanbao(text, max_retries: int = 3):
    """异步版本的 dp_api_yuanbao"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_thread_pool, dp_api_yuanbao, text, max_retries)

async def async_detail_extraction(body_html, max_retries: int = 3):
    """异步版本的 detail_extraction"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_thread_pool, detail_extraction, body_html, max_retries)

async def batch_detail_url(urls: List[str], max_retries: int = 3) -> List[Dict[str, str]]:
    """批量异步调用 detail_url
    Args:
        urls: URL列表
        max_retries: 最大重试次数
    Returns:
        结果列表，与输入URLs顺序对应
    """
    tasks = [async_detail_url(url, max_retries) for url in urls]
    return await asyncio.gather(*tasks, return_exceptions=True)

def batch_detail_url_sync(urls: List[str], max_retries: int = 3) -> List[Dict[str, str]]:
    """批量异步调用 detail_url 的同步包装器
    Args:
        urls: URL列表
        max_retries: 最大重试次数
    Returns:
        结果列表，与输入URLs顺序对应
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(batch_detail_url(urls, max_retries))

def send_dingtalk_message(content: str):
    """
    发送钉钉消息
    :param content:
    :return:
    """
    dingtalk_webhook= "https://oapi.dingtalk.com/robot/send?access_token=a2af260f8e079c06c3b9c6331773ed2e14c08d282815a79db0ed142bf4c9d53e"
    data = {
        "msgtype": "text",
        "text": {
            "content": (
                "🚨 AI爬虫异常告警！\n"
                "检测到一些错误行为，请及时检查程序！\n\n"
                f"时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} \n"
                f"详情如下：\n"
                f"{content}"
            )
        }
    }
    try:
        send_response = requests.post(dingtalk_webhook, json=data)
        result = send_response.json()

        if result.get("errcode") == 0:
            logger.info("钉钉消息发送成功!")
        else:
            logger.error(f"钉钉发送失败: {result}")
    except Exception as e:
        logger.error(f"钉钉发送异常: {e}")