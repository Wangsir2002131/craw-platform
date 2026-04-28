"""
数据库全局连接使用示例 - 数据插入函数库
包含所有表的插入函数，注意表之间的关联关系
"""
import sys
import os
import uuid
import time
from datetime import datetime
from typing import Optional, List, Dict, Callable
from functools import wraps
import pymysql

# 添加shared-methods目录到sys.path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), 'shared-methods'))
from shared_methods import db_manager, get_db_connection
import logging

logger = logging.getLogger(__name__)

# ==================== 重试装饰器 ====================
def retry_on_db_error(max_retries=3, retry_delay=1.0):
    """
    数据库操作重试装饰器
    当遇到数据库连接错误时自动重试
    Args:
        max_retries: 最大重试次数（默认3次）
        retry_delay: 重试延迟（秒，默认1秒）
    跳过重试的错误类型：
        - 数据类型错误（1366）
        - 字段过长错误（1406）
        - 外键约束错误（1452）
        - 主键重复错误（1062）
    """
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e

                    # 判断是否为可重试的错误
                    should_retry = False

                    if isinstance(e, pymysql.Error):
                        error_code = e.args[0] if e.args else 0

                        # 不可重试的错误码（数据格式/约束问题）
                        non_retryable_codes = [
                            1062,  # Duplicate entry (主键重复)
                            1366,  # Incorrect string value (数据类型错误)
                            1406,  # Data too long (字段过长)
                            1452,  # Cannot add or update a child row (外键约束)
                            1048,  # Column cannot be null (非空约束)
                            1292,  # Incorrect datetime value (日期格式错误)
                            1054,  # Unknown column (未知列)
                            1146,  # Table doesn't exist (表不存在)
                        ]

                        # 可重试的错误码（连接/网络问题）
                        retryable_codes = [
                            2003,  # Can't connect to MySQL server
                            2006,  # MySQL server has gone away
                            2013,  # Lost connection to MySQL server
                            1205,  # Lock wait timeout exceeded
                            1213,  # Deadlock found
                        ]

                        if error_code in non_retryable_codes:
                            # 数据问题，不重试，直接返回
                            logger.error(f"❌ 数据格式/约束错误 (错误码: {error_code})，不进行重试: {e}")
                            return None
                        elif error_code in retryable_codes:
                            should_retry = True
                            logger.warning(f"⚠️ 数据库连接错误 (错误码: {error_code})，准备重试...")
                        else:
                            # 未知错误码，尝试重试
                            should_retry = True
                            logger.warning(f"⚠️ 未知数据库错误 (错误码: {error_code})，尝试重试...")
                    else:
                        # 非pymysql错误，判断是否为不可重试的Python代码错误
                        error_type = type(e).__name__

                        # Python代码逻辑错误，不应该重试
                        non_retryable_python_errors = [
                            'TypeError',      # 类型错误（如None[0]，但COUNT应该不会返回None）
                            'KeyError',       # 键错误（字典访问错误）
                            'IndexError',     # 索引错误
                            'AttributeError', # 属性错误
                            'ValueError',     # 值错误（数据转换失败）
                        ]
                        
                        if error_type in non_retryable_python_errors:
                            # 代码逻辑错误，不重试
                            logger.error(f"❌ Python代码错误 ({error_type})，不进行重试: {e}")
                            return None
                        else:
                            # 其他未知错误，尝试重试
                            should_retry = True
                            logger.warning(f"⚠️ 未知错误类型: {error_type}，尝试重试...")

                    if should_retry and attempt < max_retries - 1:
                        logger.info(f"🔄 第 {attempt + 1}/{max_retries} 次重试，等待 {retry_delay} 秒...")
                        time.sleep(retry_delay)
                        # 尝试重新建立数据库连接
                        try:
                            from shared_methods import db_manager
                            db_manager._connection = None  # 强制重连
                            db_manager.get_connection()
                            logger.info("✓ 数据库重新连接成功")
                        except Exception as reconnect_error:
                            logger.error(f"✗ 重新连接失败: {reconnect_error}")
                        continue
                    else:
                        # 不重试或已达最大重试次数
                        break

            # 所有重试都失败，记录最后的错误
            logger.error(f"✗ 操作失败，已重试 {max_retries} 次: {last_exception}")
            return None

        return wrapper
    return decorator

# ==================== 工具函数 ====================
def generate_uuid_str() -> str:
    """生成UUID并转换为MySQL binary(16)格式的SQL表达式"""
    return str(uuid.uuid4())

# ==================== 表1: 任务问题回复内容表 ====================
@retry_on_db_error(max_retries=3, retry_delay=1.0)
def insert_task_question_reply_content(
    question_id: str,
    product_llm_task_id: Optional[str] = None,
    llm_search_term: Optional[str] = None,
    reply_content: Optional[str] = None,
    round_num: int = 1
) -> Optional[str]:
    """
    插入任务问题回复内容
    Args:
        question_id: 问题ID（必填）
        product_llm_task_id: 产品任务模型表id（必填）
        llm_search_term: 模型检索词（必填）
        reply_content: 回复内容（必填）
        round_num: 执行轮次（默认1）
    插入数据规则:
        若 QuestionId + ProductLlmTaskId 已存在，则跳过插入
    Returns:
        返回插入的 TaskQuestionReplyContentId，失败返回None
    """
    # 如果没有提供product_llm_task_id，记录错误并返回
    if not product_llm_task_id:
        logger.error("❌ 缺少ProductLlmTaskId，无法插入回复内容")
        return None
    with db_manager.get_cursor() as cursor:
        # # 先判断是否已存在
        # check_sql = """
        #     SELECT TaskQuestionReplyContentId
        #     FROM tmp_data_task_qusetion_reply_content
        #     WHERE QuestionId = %s
        #       AND ProductLlmTaskId = %s
        #       AND Deleted = 0
        #       LIMIT 1;
        # """
        # cursor.execute(check_sql, (question_id, product_llm_task_id))
        # row = cursor.fetchone()
        #
        # if row:
        #     exist_id = row["TaskQuestionReplyContentId"] if isinstance(row, dict) else row[0]
        #     logger.info(f"⏭ 已存在回复内容，跳过插入 [QuestionId={question_id}, ProductLlmTaskId={product_llm_task_id}]")
        #     return exist_id
        # 不存在才插入
        task_question_reply_content_id = generate_uuid_str()
        insert_sql = """
            INSERT INTO tmp_data_task_qusetion_reply_content 
             (TaskQuestionReplyContentId, QuestionId, ProductLlmTaskId, 
              LlmSearchTerm, ReplyContent, Round, Disabled, Deleted, CreatedTime) 
             VALUES (%s, %s, %s, %s, %s, %s, 0, 0, NOW())
        """
        cursor.execute(insert_sql, (
            task_question_reply_content_id,
            question_id,
            product_llm_task_id,
            llm_search_term or '',
            reply_content or '',
            round_num
        ))
    logger.info(f"✓ 插入回复内容成功 [TaskQuestionReplyContentId: {task_question_reply_content_id}]")
    return task_question_reply_content_id

# ==================== 表2: 任务问题回复内容片段表 ====================
@retry_on_db_error(max_retries=3, retry_delay=1.0)
def insert_task_question_reply_content_snippet(
    task_question_reply_content_id: str,
    task_question_search_web_id: str,
    reply_content_snippet: Optional[str] = None
) -> Optional[str]:
    """
    插入任务问题回复内容片段
    Args:
        task_question_reply_content_id: 关联表1的ID（必填）
        task_question_search_web_id: 关联表3的ID（必填）- 网页ID
        reply_content_snippet: 回复内容片段（必填）
    Returns:
        返回插入的 TaskQuestionReplyContentSnippetId，失败返回None
    """
    task_question_reply_content_snippet_id = generate_uuid_str()
    # 检查必须的外键参数
    if not task_question_reply_content_id:
        logger.error("❌ 缺少TaskQuestionReplyContentId，无法插入回复片段")
        return None
    if not task_question_search_web_id:
        logger.error("❌ 缺少TaskQuestionSearchWebId，无法插入回复片段")
        return None
    with db_manager.get_cursor() as cursor:
        sql = """INSERT INTO tmp_data_task_qusetion_reply_content_snippet 
                 (TaskQuestionReplyContentSnippetId, TaskQuestionReplyContentId, 
                  TaskQuestionSearchWebId, ReplyContentSnippet, Disabled, Deleted, CreatedTime) 
                 VALUES (%s, %s, %s, %s, 0, 0, NOW())"""
        cursor.execute(sql, (
            task_question_reply_content_snippet_id,
            task_question_reply_content_id,
            task_question_search_web_id,
            reply_content_snippet or ''
        ))
    logger.info(f"✓ 插入回复片段成功 [SnippetId: {task_question_reply_content_snippet_id}]")
    return task_question_reply_content_snippet_id

# ==================== 表3: 任务检索网站表 ====================
@retry_on_db_error(max_retries=3, retry_delay=1.0)
def insert_task_question_search_web(
    task_question_search_web_id: str,
    question_id: str,
    product_llm_task_id: Optional[str] = None,
    site_name: Optional[str] = None,
    site_title: Optional[str] = None,
    author: Optional[str] = None,
    publish_time: Optional[str] = None,
    content: Optional[str] = None,
    site_url: Optional[str] = None,
    site_sort: int = 0,
    is_cite: int = 0,
    oss_pdf_url: Optional[str] = None,
    round_num: int = 1
) -> Optional[str]:
    """
    插入任务检索网站
    Args:
        question_id: 问题ID（必填）
        product_llm_task_id: 产品任务模型表id（必填）
        site_name: 网站名称（必填）
        site_title: 网站标题（必填）
        author: 作者（必填）
        publish_time: 发布时间（必填，格式：YYYY-MM-DD HH:MM:SS）
        content: 内容（必填）
        site_url: 网站URL（必填）
        round_num: 执行轮次（默认1）
    Returns:
        返回插入的 TaskQuestionSearchWebId，失败返回None
    """
    # 如果没有提供product_llm_task_id，记录错误并返回
    if not product_llm_task_id:
        logger.error("❌ 缺少ProductLlmTaskId，无法插入搜索网站")
        return None
    # 处理发布时间
    if publish_time:
        # 尝试解析时间字符串
        try:
            # 如果是字符串格式的时间，直接使用
            publish_time_sql = publish_time
        except:
            publish_time_sql = '1970-01-01 00:00:00'
    else:
        publish_time_sql = '1970-01-01 00:00:00'
    with db_manager.get_cursor() as cursor:
        sql = """INSERT INTO tmp_data_task_question_search_web 
                 (TaskQuestionSearchWebId, QuestionId, ProductLlmTaskId, 
                  SiteName, SiteTitle, Author, PublishTime, Content, SiteUrl, SiteSort, IsCite,
                  OssPdfUrl, Round, Disabled, Deleted, CreatedTime) 
                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, 0, NOW())"""
        cursor.execute(sql, (
            task_question_search_web_id,
            question_id,
            product_llm_task_id,
            site_name or '',
            site_title or '',
            author or '',
            publish_time_sql,
            content or '',
            site_url or '',
            site_sort,
            is_cite,
            oss_pdf_url or '',
            round_num
        ))
    logger.info(f"✓ 插入检索网站成功 [SearchWebId: {task_question_search_web_id}]")
    return task_question_search_web_id

# ==================== 表4: 问题网站内容表 ====================
@retry_on_db_error(max_retries=3, retry_delay=1.0)
def insert_product_llm_task_web_content(
    product_llm_task_id: str,
    question_id: str,
    content: Optional[str] = None,
    web_url: Optional[str] = None,
    is_cite: int = 0
) -> Optional[str]:
    """
    插入问题网站内容
    Args:
        product_llm_task_id: 产品任务模型表id（必填）
        question_id: 问题ID（必填）
        content: 网站内容（可选）
        web_url: 网站URL（可选）
        is_cite: 是否引用，0=无引用片段，1=有引用片段（默认0）
    插入数据规则：
        - 按 ProductLImTaskId + QuestionId + WebUrl 统计
        - 已存在 >=2 条：跳过
        - 已存在 0 或 1 条：允许插入
    Returns:
        返回插入的 ProductllmTaskWebContentId，失败返回None
    """
    # if not product_llm_task_id or not question_id or not web_url:
    if not product_llm_task_id or not question_id:
        logger.error("❌ 参数不完整，无法插入网站内容")
        return None
    with db_manager.get_cursor() as cursor:
        # # 统计已存在数量
        # count_sql = """
        #     SELECT COUNT(1) as cnt
        #     FROM tmp_data_product_llm_task_web_content
        #     WHERE ProductLImTaskId = %s
        #       AND QuestionId = %s
        #       AND WebUrl = %s
        #       AND Deleted = 0
        # """
        # cursor.execute(count_sql, (
        #     product_llm_task_id,
        #     question_id,
        #     web_url
        # ))
        # result = cursor.fetchone()
        #
        # # 处理不同类型的cursor返回结果
        # if result is None:
        #     count = 0
        # elif isinstance(result, dict):
        #     # DictCursor返回字典
        #     count = result.get('cnt', 0) or 0
        # elif isinstance(result, tuple):
        #     # 普通cursor返回元组
        #     count = result[0] if (result and result[0] is not None) else 0
        # else:
        #     # 其他情况，尝试直接访问[0]
        #     count = result[0] if result[0] is not None else 0
        #
        # if count >= 2:
        #     logger.info(f"⏭ 已存在 {count} 条网站内容，跳过插入 [ProductLImTaskId={product_llm_task_id}, QuestionId={question_id}, WebUrl={web_url}]")
        #     return None
        product_llm_task_web_content_id = generate_uuid_str()
        insert_sql = """
            INSERT INTO tmp_data_product_llm_task_web_content 
            (ProductllmTaskWebContentld, ProductLImTaskId, QuestionId, 
            Content, WebUrl, IsCite, Disabled, Deleted, CreatedTime) 
            VALUES (%s, %s, %s, %s, %s, %s, 0, 0, NOW())
        """
        cursor.execute(insert_sql, (
            product_llm_task_web_content_id,
            product_llm_task_id,
            question_id,
            content or '',
            web_url,
            is_cite
        ))
    logger.info(f"✓ 插入网站内容成功 [WebContentId: {product_llm_task_web_content_id}, IsCite: {is_cite}]")
    return product_llm_task_web_content_id

# ==================== 高级函数：保存完整答案（含片段和搜索结果）====================
def save_complete_answer(
    question_id: str,
    product_llm_task_id: str,
    llm_search_term: str,
    reply_content: str,
    snippets_with_citations: List[Dict] = None,
    search_web_id_map: Dict[str, str] = None
) -> Optional[str]:
    """
    保存完整的答案（包括回复内容和片段）
    Args:
        question_id: 问题ID
        product_llm_task_id: 产品任务模型表id
        llm_search_term: 模型检索词
        reply_content: 回复内容
        snippets_with_citations: 片段列表 [{'content': '...', 'citation': '引用编号'}]
        search_web_id_map: 引用编号到SearchWebId的映射 {'1': 'uuid...', '2': 'uuid...'}
    Returns:
        返回插入的 TaskQuestionReplyContentId
    """
    # 1. 插入回复内容
    reply_content_id = insert_task_question_reply_content(
        question_id=question_id,
        product_llm_task_id=product_llm_task_id,
        llm_search_term=llm_search_term,
        reply_content=reply_content
    )
    if not reply_content_id:
        return None
    # 2. 插入片段（如果有）
    if snippets_with_citations and search_web_id_map:
        for snippet in snippets_with_citations:
            citation = snippet.get('citation', '')
            content = snippet.get('content', '')
            # 根据引用编号获取对应的SearchWebId
            search_web_id = search_web_id_map.get(citation)
            if search_web_id:
                insert_task_question_reply_content_snippet(
                    task_question_reply_content_id=reply_content_id,
                    task_question_search_web_id=search_web_id,
                    reply_content_snippet=content
                )
    return reply_content_id

# ==================== 表5: 产品模型任务表状态更新 ====================
@retry_on_db_error(max_retries=3, retry_delay=1.0)
def update_product_llm_task_status(
    product_llm_task_id: str,
    status: str
) -> bool:
    """
    更新产品模型任务表的状态
    Args:
        product_llm_task_id: 产品模型任务ID
        status: 目标状态（如 '进行中', '爬网完成'）
    Returns:
        成功返回True，失败返回None
    """
    if not product_llm_task_id:
        logger.error("❌ 缺少ProductLlmTaskId，无法更新状态")
        return None
        
    with db_manager.get_cursor() as cursor:
        sql = """
            UPDATE ent_data_product_llm_task 
            SET Status = %s, UpdatedTime = NOW()
            WHERE ProductLlmTaskId = %s
        """
        cursor.execute(sql, (status, product_llm_task_id))
    
    logger.info(f"✓ 任务状态更新成功 [ProductLlmTaskId: {product_llm_task_id}, Status: {status}]")
    return True

# ==================== 表6: 产品模型情绪分析结果表 ====================
def update_product_llm_emotion_reply(
    product_llm_emotion_id: str,
    reply: str,
    product: str
) -> bool:
    """
    更新产品模型情绪分析的回复内容
    Args:
        product_llm_emotion_id: 情绪分析记录ID（必填）
        reply: AI回复内容（必填）
    Returns:
        成功返回True，失败返回False
    """
    try:
        with db_manager.get_cursor() as cursor:
            sql = """
                UPDATE ent_data_product_llm_emotion 
                SET Reply = %s, ProductPreference = %s, UpdatedTime = NOW()
                WHERE ProductLlmEmotionId = %s
            """
            cursor.execute(sql, (reply, product, product_llm_emotion_id))
        logger.info(f"✓ 已更新情绪分析Reply [ID: {product_llm_emotion_id}]")
        return True
    except Exception as e:
        logger.error(f"✗ 更新情绪分析Reply失败: {e}")
        return False

# ==================== 工具函数：获取默认ProductLlmTaskId ====================
def get_default_product_llm_task_id() -> str:
    """
    获取默认的ProductLlmTaskId（占位函数）
    注意：此函数仅用于向后兼容，实际的ProductLlmTaskId应由main.py从数据库查询后传递
    不应在此函数中查询数据库，因为main.py已经统一查询了所有任务数据
    Returns:
        返回None，实际值由main.py传入的问题数据中获取
    """
    logger.warning("⚠️ get_default_product_llm_task_id() 已废弃，ProductLlmTaskId应由main.py传入")
    return None

if __name__ == '__main__':
    print("=" * 60)
    print("数据库插入函数库 - 使用说明")
    print("=" * 60)
    print("此文件包含所有表的插入函数，在 *2.py 文件中导入使用：")
    print("from database_usage_example import (")
    print("insert_task_question_reply_content,")
    print("insert_task_question_reply_content_snippet,")
    print("insert_task_question_search_web,")
    print("insert_product_llm_task_web_content,")
    print("batch_insert_reply_content_snippets")
    print(")")
    print("使用示例：")
    print("# 1. 插入回复内容")
    print("reply_id = insert_task_question_reply_content(question_id, reply_content=text, llm_name='deepseek')")
    print("# 2. 插入回复片段")
    print("batch_insert_reply_content_snippets(reply_id, snippets)")
    print("# 3. 插入检索网站")
    print("insert_task_question_search_web(question_id, site_name=name, site_url=url)")
