# 问题内容，片段，web                                          # 说明本模块处理的数据类型：问题内容、片段、web引用
# 和afu一样的逻辑                                              # 说明本模块逻辑与 afu 爬虫类似
# & "C:\Program Files\Google\Chrome\Application\chrome.exe" --user-data-dir="D:\doubao_real_profiles\account_1"  # 手动启动 Chrome 并指定用户数据目录的命令示例
import random                                                   # 导入 random 模块，用于生成随机数（模拟人工延迟等）
import os                                                       # 导入 os 模块，用于文件路径操作和环境变量读取
import sys                                                      # 导入 sys 模块，用于修改模块搜索路径
import time                                                     # 导入 time 模块，用于程序暂停（sleep）
import threading                                                # 导入 threading 模块，用于多线程并发控制和锁
import logging                                                  # 导入 logging 模块，用于日志记录
import traceback                                                # 导入 traceback 模块，用于打印异常堆栈信息
import zipfile                                                  # 导入 zipfile 模块，用于创建代理认证插件的 zip 包
from typing import List, Optional, Callable                     # 导入类型注解，用于函数签名的类型提示
from concurrent.futures import ThreadPoolExecutor, as_completed # 导入线程池和 Future 完成迭代器，用于并发执行任务

try:                                                            # 尝试导入 pymysql（MySQL 数据库驱动）
    import pymysql                                              # 成功则可用于数据库连接
except ImportError:                                             # 如果未安装 pymysql
    pymysql = None                                              # 设为 None，后续代码会检查是否可用

try:                                                            # 尝试导入 selenium 相关模块（浏览器自动化）
    from selenium import webdriver                              # 导入 webdriver，用于创建浏览器实例
    from selenium.webdriver.common.by import By                 # 导入 By，用于指定元素定位方式（CSS、XPATH等）
    from selenium.webdriver.common.keys import Keys             # 导入 Keys，用于模拟键盘按键（如回车）
    from selenium.webdriver.support.ui import WebDriverWait     # 导入 WebDriverWait，用于显式等待元素出现
    from selenium.webdriver.support import expected_conditions as EC  # 导入 EC，提供预定义的等待条件
    from selenium.webdriver.chrome.service import Service       # 导入 Service，用于指定 chromedriver 路径
    from selenium.webdriver.chrome.options import Options       # 导入 Options，用于配置 Chrome 启动参数
    from selenium.common.exceptions import TimeoutException, NoSuchElementException  # 导入常见异常类
except ImportError:                                             # 如果 selenium 未安装
    webdriver = None                                            # 所有 selenium 相关对象设为 None 或默认值
    By = None
    Keys = None
    WebDriverWait = None
    EC = None
    Service = None
    Options = None
    TimeoutException = Exception                                # 用基础 Exception 作为替代，避免运行时引用报错
    NoSuchElementException = Exception

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), 'shared-methods'))  # 将上级目录的 shared-methods 加入模块搜索路径
try:                                                            # 尝试从 shared_methods 导入公共工具函数
    from shared_methods import dp_api_afu, send_dingtalk_message, DB_CONFIG, get_proxy  # dp_api_afu: API调用工具; send_dingtalk_message: 钉钉告警; DB_CONFIG: 数据库配置; get_proxy: 获取代理
except Exception:                                               # 导入失败时提供降级实现
    dp_api_afu = None                                           # API 工具不可用
    DB_CONFIG = {}                                              # 数据库配置为空字典

    def send_dingtalk_message(*args, **kwargs):                 # 钉钉消息降级为空操作
        return None

    def get_proxy(*args, **kwargs):                             # 获取代理降级为返回 None
        return None

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))  # 将项目根目录加入模块搜索路径
try:                                                            # 尝试导入账号分配器
    from platform.account.account_allocator import AccountAllocator  # AccountAllocator 负责多账号的分配和释放
except Exception:                                               # 导入失败（如 platform 目录不存在）
    AccountAllocator = None                                     # 设为 None，后续会检查

try:                                                            # 尝试导入数据库操作函数
    from database_usage_example import (
        insert_task_question_reply_content,                      # 插入问题的 AI 回复内容到数据库
        insert_task_question_reply_content_snippet,              # 插入回复内容的引用片段
        insert_product_llm_task_web_content,                     # 插入搜索引用的网页内容记录
        insert_task_question_search_web,                         # 插入搜索结果详细信息
        update_product_llm_task_status,                          # 更新任务状态（未开始/进行中/爬网完成）
        get_default_product_llm_task_id                          # 获取默认的产品LLM任务ID
    )
except Exception:                                               # 导入失败时提供所有函数的降级空实现
    def insert_task_question_reply_content(*args, **kwargs):     # 降级：不执行实际插入
        return None

    def insert_task_question_reply_content_snippet(*args, **kwargs):  # 降级：不执行实际插入
        return None

    def insert_product_llm_task_web_content(*args, **kwargs):    # 降级：不执行实际插入
        return None

    def insert_task_question_search_web(*args, **kwargs):        # 降级：不执行实际插入
        return None

    def update_product_llm_task_status(*args, **kwargs):         # 降级：不执行实际更新
        return None

    def get_default_product_llm_task_id():                       # 降级：返回 None
        return None

logger = logging.getLogger(__name__)                            # 创建当前模块的 logger 实例
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")  # 配置日志格式：时间 + 级别 + 消息

# 屏蔽 urllib3 连接池满的警告
logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)  # 将 urllib3 连接池日志级别设为 ERROR，过滤掉 WARNING 级别的连接池满提示

# 全局 profile 占用锁，防止并发重试时多个任务争抢同一个 profile
_profile_lock = threading.Lock()                                # 创建全局线程锁，用于保护 _profiles_in_use 集合
_profiles_in_use: set = set()                                   # 记录当前正在被使用的 profile 名称集合


class DouBao:
    """豆包（DouBao）AI 爬虫类，负责通过 Selenium 驱动 Chrome 浏览器与豆包网页版交互，提问并获取回答和引用"""

    def __init__(self, account_id: str, use_proxy: bool = True, proxy_api: Optional[Callable] = None, executable_path: Optional[str] = None):
        """初始化豆包爬虫实例
        Args:
            account_id: 账号标识（对应 profile 目录编号）
            use_proxy: 是否启用代理
            proxy_api: 自定义代理获取函数（可选）
            executable_path: Chrome 可执行文件路径（可选）
        """
        self.browser = None                                     # 浏览器对象占位（未使用，保留兼容）
        self.page = None                                        # 页面对象占位（未使用，保留兼容）
        self.context = None                                     # 上下
