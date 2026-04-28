#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI模型并发调度系统 - 集成数据库监听功能
监听 ent_data_product_llm_task 表的新数据，获取任务数据后按模型分组并发处理
"""

import os
import sys
import logging
import time
from typing import List, Tuple, Dict
import pymysql
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# 配置日志
logger = logging.getLogger(__name__)

# 添加shared-methods目录到sys.path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), 'shared-methods'))
from shared_methods import get_proxy, DB_CONFIG

# 导入各个AI模型模块
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), 'deepseek'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), 'yuanbao'))

from deepseek import run_concurrent as deepseek_run_concurrent
from yuanbao import run_concurrent as yuanbao_run_concurrent


class DatabaseListener:
    """数据库监听器 - 负责监听数据库并获取任务数据"""

    def __init__(self, start_time: str = None):
        """
        初始化监听器
        Args:
            start_time: 起始查询时间，格式：'****-**-** **:**:**'，如果为None则从当前时间开始
        """
        self.last_check_time = start_time
        self.processed_tasks = set()  # 记录已处理的任务ID

    def get_db_connection(self):
        """获取数据库连接"""
        try:
            return pymysql.connect(**DB_CONFIG)
        except Exception as e:
            logger.error(f"数据库连接失败: {e}")
            return None

    def get_new_tasks(self) -> List[Dict]:
        """
        获取新增的任务数据，并关联查询所有相关的问题信息
        Returns:
            任务列表，每个任务-问题组合包含 ProductLlmTaskId, ProductTaskId, ProductId, LlmKey, QuestionName
        """
        conn = self.get_db_connection()
        if not conn:
            return []

        cursor = None
        try:
            # 使用DictCursor，自动将结果转为字典
            cursor = conn.cursor(pymysql.cursors.DictCursor)

            # 构建查询条件
            where_conditions = [
                "llm_task.Deleted = b'0'",
                "llm_task.Disabled = b'0'",
                "llm_task.Status = '未开始'"
            ]

            query_params = []
            if self.last_check_time:
                where_conditions.append("llm_task.CreatedTime >= %s")
                query_params.append(self.last_check_time)

            where_sql = " AND ".join(where_conditions)

            # 查询新增任务及其关联的问题（一对多展开）
            sql = f"""
            SELECT
                llm_task.ProductLlmTaskId,
                llm_task.ProductTaskId,
                llm_task.ProductId,
                llm_task.LlmKey,
                llm_task.CreatedTime,
                llm_task.MaxRounds,
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
            WHERE {where_sql}
            ORDER BY llm_task.CreatedTime ASC, prod_question.CreatedTime ASC;
            """

            cursor.execute(sql, query_params)
            rows = cursor.fetchall()

            if not rows:
                return []

            # 更新最后检查时间
            max_time = max(row['CreatedTime'] for row in rows)
            if isinstance(max_time, datetime):
                self.last_check_time = max_time.strftime('%Y-%m-%d %H:%M:%S')
            else:
                self.last_check_time = str(max_time)

            # 将数据按任务分组，每个任务-问题组合返回一条记录
            result = []
            task_question_map = {}  # 用于去重

            for row in rows:
                task_id = row['ProductLlmTaskId']
                question_name = row['QuestionName']

                # 如果没有问题名称，跳过
                if not question_name:
                    continue

                # 创建唯一键，避免同一任务同一问题重复
                key = f"{task_id}_{question_name}"
                if key in task_question_map:
                    continue

                task_question_map[key] = True

                # 创建任务 - 问题组合记录
                task_record = {
                    'ProductLlmTaskId': task_id,
                    'ProductTaskId': row['ProductTaskId'],
                    'ProductId': row['ProductId'],
                    'LlmKey': row['LlmKey'],
                    'CreatedTime': row['CreatedTime'],
                    'MaxRounds': row['MaxRounds'],  # 添加最大轮次
                    'QuestionId': row.get('QuestionId'),
                    'QuestionName': question_name
                }
                result.append(task_record)

            # 优化日志输出：只显示汇总信息
            logger.info(f"🔍 查询到 {len(result)} 个任务-问题组合")
            if result:
                # 按模型统计数量
                model_counts = {}
                for record in result:
                    llm_key = record.get('LlmKey', 'Unknown')
                    model_counts[llm_key] = model_counts.get(llm_key, 0) + 1

                for model, count in model_counts.items():
                    logger.info(f"{model}: {count} 个任务-问题组合")

                # DEBUG级别显示详细信息
                logger.debug("详细任务列表：")
                for record in result:
                    question_id = record.get('QuestionId', 'N/A')
                    logger.debug(f"任务ID: {record['ProductLlmTaskId']}, 问题: '{record['QuestionName']}' (问题ID: {question_id})")

            return result

        except Exception as e:
            logger.error(f"查询新任务失败: {e}", exc_info=True)
            return []
        finally:
            # 确保资源释放
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    def get_processed_tasks(self):
        """获取已处理的任务ID集合"""
        return self.processed_tasks

    def update_task_status(self, product_llm_task_id: str, status: str) -> bool:
        """
        更新任务状态
        Args:
            product_llm_task_id: 任务ID
            status: 新状态 (进行中/爬网完成/已失败)
        Returns:
            更新成功返回True，失败返回False
        """
        conn = self.get_db_connection()
        if not conn:
            return False
        try:
            cursor = conn.cursor()
            sql = """
                UPDATE ent_data_product_llm_task 
                SET Status = %s, UpdatedTime = NOW() 
                WHERE ProductLlmTaskId = %s
            """
            cursor.execute(sql, (status, product_llm_task_id))
            conn.commit()
            cursor.close()
            conn.close()
            logger.info(f"✓ 任务 {product_llm_task_id} 状态已更新为: {status}")
            return True
        except Exception as e:
            logger.error(f"更新任务状态失败 {product_llm_task_id}: {e}")
            if conn:
                conn.rollback()
                conn.close()
            return False

    def batch_update_task_status(self, product_llm_task_ids: List[str], status: str) -> bool:
        """
        批量更新任务状态
        Args:
            product_llm_task_ids: 任务ID列表
            status: 新状态 (进行中/爬网完成/已失败)
        Returns:
            更新成功返回True，失败返回False
        """
        if not product_llm_task_ids:
            return True

        conn = self.get_db_connection()
        if not conn:
            return False
        try:
            cursor = conn.cursor()
            # 构建批量更新SQL
            placeholders = ', '.join(['%s'] * len(product_llm_task_ids))
            sql = f"""
                UPDATE ent_data_product_llm_task 
                SET Status = %s, UpdatedTime = NOW() 
                WHERE ProductLlmTaskId IN ({placeholders})
            """
            cursor.execute(sql, [status] + product_llm_task_ids)
            conn.commit()
            affected_rows = cursor.rowcount
            cursor.close()
            conn.close()
            logger.info(f"✓ 批量更新 {affected_rows} 个任务状态为: {status}")
            return True
        except Exception as e:
            logger.error(f"批量更新任务状态失败: {e}")
            if conn:
                conn.rollback()
                conn.close()
            return False


class AIModelParallelRunner:
    """AI 模型并发调度器 - 支持多个平台同时运行"""
    def __init__(self, max_workers: int = 4, cookie_workers: int = 1, proxy_api=None, exclude_models: List[str] = None, db_listener=None, repeat_count: int = None, round_num: int = None):
        """
        初始化 AI 模型并发调度器
        Args:
            max_workers: 平台并发数（同时运行几个平台，如 4 表示四个平台同时运行）
            cookie_workers: 每个平台内部的 cookie 并发数（每个平台内部 cookie 的并发数）
            proxy_api: 代理 API 调用函数 (可选)
            exclude_models: 要排除的模型列表 (如 ['yuanbao'])
            db_listener: 数据库监听器实例，用于更新任务状态
            repeat_count: 每个问题重复提问的次数（已废弃，改为从数据库 MaxRounds 字段读取）
            round_num: 执行轮次（默认 1，已废弃）
        """
        self.max_workers = max_workers
        self.cookie_workers = cookie_workers
        self.proxy_api = proxy_api
        self.exclude_models = [m.lower() for m in (exclude_models or [])]
        self.db_listener = db_listener
        self.repeat_count = repeat_count  # 保留但不再使用，仅用于兼容
        self.round_num = round_num  # 保留但不再使用，仅用于兼容
        # AI模型映射表
        self.model_map = {
            'deepseek': {
                'name': 'DeepSeek',
                'runner': deepseek_run_concurrent
            },
            'yuanbao': {
                'name': 'YuanBao',
                'runner': yuanbao_run_concurrent
            }
        }

    def group_tasks_by_model(self, tasks: List[Dict]) -> Dict[str, List[Tuple]]:
        """
        将任务按模型分组，转换为 main.py 需要的格式
        Args:
            tasks: 任务列表，每个任务包含 ProductLlmTaskId, ProductTaskId, ProductId, LlmKey, QuestionName, QuestionId, MaxRounds
        Returns:
            字典，key 为模型名称，value 为问题列表 [(question_id, question_name, product_llm_task_id, task_name, product_task_id), ...]
        """
        grouped_questions = {}
        for task in tasks:
            llm_key = task.get('LlmKey')
            if not llm_key:
                logger.warning(f"跳过没有 LlmKey 的任务：{task.get('ProductLlmTaskId')}")
                continue
            model_key = llm_key.strip().lower()
            if model_key not in grouped_questions:
                grouped_questions[model_key] = []

            # 从数据库中获取 MaxRounds，如果为空则默认为 1
            max_rounds = task.get('MaxRounds', 1)
            if max_rounds is None:
                max_rounds = 1

            # 构建与 main.py 兼容的元组格式
            # (question_id, question_name, product_llm_task_id, task_name, product_task_id, round_num)
            question_base = (
                task.get('QuestionId', task['ProductLlmTaskId']),  # question_id (使用实际 QuestionId)
                task['QuestionName'],  # question_name
                task['ProductLlmTaskId'],  # product_llm_task_id
                f"Task_{task['ProductLlmTaskId']}",  # task_name (从 ProductTaskId 获取更合适，这里简化处理)
                task['ProductTaskId']  # product_task_id
            )
            
            # 将该问题的基础信息添加到临时列表，后续按轮次交错展开
            grouped_questions[model_key].append((question_base, max_rounds))

        final_grouped = {}
        for model_key, question_list in grouped_questions.items():
            expanded_questions = []
            
            # 找到最大的轮次数
            global_max_rounds = max(max_rounds for _, max_rounds in question_list)
            
            # 按轮次交错展开：先所有问题的第 1 轮，再所有问题的第 2 轮...
            for round_idx in range(1, global_max_rounds + 1):
                for question_base, max_rounds in question_list:
                    # 如果该问题的轮次还没到上限，则添加
                    if round_idx <= max_rounds:
                        expanded_question = question_base + (round_idx,)  # 添加 round_num
                        expanded_questions.append(expanded_question)
            
            final_grouped[model_key] = expanded_questions

        # 打印分组情况
        logger.info("=" * 60)
        logger.info("📋 问题分配情况：")
        for llm_name, questions in final_grouped.items():
            logger.info(f"✓ {llm_name} 跑 {len(questions)} 个任务（交错排列）")
        logger.info("=" * 60)

        return final_grouped

    def run_single_model(self, model_name: str, questions: List[Tuple]) -> bool:
        """
        运行单个 AI 模型（在独立线程中执行）
        Args:
            model_name: 模型名称（deepseek/yuanbao）
            questions: 问题列表 [(question_id, question_name, product_llm_task_id, task_name, product_task_id), ...]
        Returns:
            成功返回True，失败返回False
        """
        model_info = self.model_map.get(model_name)

        if not model_info:
            logger.error(f"❌ 未知的 AI 模型: {model_name}")
            return False

        if not questions:
            logger.warning(f"⚠️  {model_info['name']} 没有需要处理的问题，跳过")
            return True

        # 提取所有任务ID（去重）
        task_ids = list(set(q[2] for q in questions))  # product_llm_task_id 在索引2

        # 🔥 开始前：将任务状态更新为"进行中"
        if self.db_listener:
            logger.info(f"📝 [{model_info['name']}] 更新 {len(task_ids)} 个任务状态为: 进行中")
            self.db_listener.batch_update_task_status(task_ids, '进行中')

        logger.info(f"{'=' * 60}")
        logger.info(f"🚀 [{model_info['name']}] 开始运行，共 {len(questions)} 个问题")
        logger.info(f"Cookie并发数: {self.cookie_workers}")
        logger.info(f"{'=' * 60}")

        try:
            # 调用run_concurrent函数，传递代理配置和cookie并发数
            model_info['runner'](
                max_workers=self.cookie_workers,  # 每个平台内部的cookie并发数
                proxy_api=self.proxy_api,
                questions=questions
            )
            logger.info(f"✅ [{model_info['name']}] 运行完成")

            # 🔥 完成后：将任务状态更新为"爬网完成"
            if self.db_listener:
                logger.info(f"📝 [{model_info['name']}] 更新 {len(task_ids)} 个任务状态为: 爬网完成")
                self.db_listener.batch_update_task_status(task_ids, '爬网完成')

            return True
        except Exception as e:
            logger.error(f"❌ [{model_info['name']}] 运行失败: {e}", exc_info=True)

            # # 🔥 失败时：将任务状态更新为"已失败"
            # if self.db_listener:
            #     logger.warning(f"📝 [{model_info['name']}] 更新 {len(task_ids)} 个任务状态为: 已失败")
            #     self.db_listener.batch_update_task_status(task_ids, '已失败')

            return False

    def run_grouped_questions(self, grouped_questions: Dict[str, List[Tuple]]):
        """运行分组后的问题（保持原有main.py的逻辑）"""
        if not grouped_questions:
            logger.error("❌ 没有任何模型有待处理的问题")
            return
        # 🔥 关键修改：使用线程池并发运行多个模型
        logger.info("=" * 60)
        logger.info(f"🚀 开始并发运行所有模型（平台并发数: {self.max_workers}）")
        logger.info("=" * 60)
        # 准备任务列表
        tasks = []
        for model_name, questions in grouped_questions.items():
            # 检查是否在排除列表中
            if model_name.lower() in self.exclude_models:
                logger.warning(f"⚠️  {model_name} 在排除列表中，跳过")
                continue
            if questions:
                tasks.append((model_name, questions, self.round_num))
        if not tasks:
            logger.error("❌ 没有任何模型有待处理的问题")
            return
        # 使用线程池并发执行所有模型
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # 提交所有模型任务
            futures = {
                executor.submit(self.run_single_model, model_name, questions): model_name
                for model_name, questions, round_num in tasks
            }
            # 等待所有任务完成
            completed = 0
            total_tasks = len(futures)
            for future in as_completed(futures):
                model_name = futures[future]
                completed += 1
                try:
                    result = future.result()
                    model_info = self.model_map.get(model_name, {})
                    model_display_name = model_info.get('name', model_name)
                    if result:
                        logger.info(f"✅ [{completed}/{total_tasks}] {model_display_name} 处理成功")
                    else:
                        logger.error(f"❌ [{completed}/{total_tasks}] {model_display_name} 处理失败")
                except Exception as e:
                    logger.error(f"❌ [{completed}/{total_tasks}] {model_name} 执行异常: {e}")
        logger.info("=" * 60)
        logger.info("🎉 所有AI模型运行完成！")
        logger.info("=" * 60)


class IntegratedRunner:
    """集成运行器 - 结合数据库监听和 AI 模型处理"""
    def __init__(self, check_interval: int = 600, start_time: str = None,
                 max_workers: int = 1, cookie_workers: int = 1, proxy_api=None, exclude_models: List[str] = None, round_num: int = 1):
        """
        初始化集成运行器
        Args:
            check_interval: 数据库检查间隔（秒）
            start_time: 起始查询时间
            max_workers: 平台并发数
            cookie_workers: 每个平台内部的 cookie 并发数
            proxy_api: 代理 API
            exclude_models: 排除的模型列表
            round_num: 执行轮次（默认 1，已废弃，轮次由数据库 MaxRounds 字段控制）
        """
        self.check_interval = check_interval
        self.database_listener = DatabaseListener(start_time)
        self.ai_runner = AIModelParallelRunner(max_workers, cookie_workers, proxy_api, exclude_models, self.database_listener, round_num=round_num)

    def start_monitoring(self):
        """开始监听数据库并处理任务"""
        logger.info("=" * 60)
        logger.info("🎯 集成系统启动 (数据库监听 + AI模型处理)")
        logger.info(f"检查间隔: {self.check_interval} 秒")
        logger.info(f"起始时间: {self.database_listener.last_check_time or '当前时间'}")
        logger.info("=" * 60)
        try:
            while True:
                logger.info(f"🔍 开始检查新任务 (上次检查: {self.database_listener.last_check_time or '首次'})")
                # 获取新任务
                new_tasks = self.database_listener.get_new_tasks()
                if new_tasks:
                    logger.info(f"📋 发现 {len(new_tasks)} 个新任务")
                    # 记录需要处理的任务
                    tasks_to_process = []
                    processed_task_ids = set()

                    for task in new_tasks:
                        task_id = task['ProductLlmTaskId']
                        question_name = task['QuestionName']
                        # 使用任务ID+问题名称组合作为唯一键，避免重复处理
                        task_question_key = f"{task_id}_{question_name}"
                        if task_question_key in self.database_listener.processed_tasks:
                            logger.info(f"⚠️ 任务-问题组合 {task_id}:{question_name} 已处理过，跳过")
                            continue
                        tasks_to_process.append(task)
                        processed_task_ids.add(task_question_key)

                    if tasks_to_process:
                        logger.info(f"🎯 准备处理 {len(tasks_to_process)} 个新任务")
                        # 将任务按模型分组
                        grouped_questions = self.ai_runner.group_tasks_by_model(tasks_to_process)
                        if grouped_questions:
                            # 运行AI模型处理
                            self.ai_runner.run_grouped_questions(grouped_questions)
                            # 标记任务为已处理
                            self.database_listener.processed_tasks.update(processed_task_ids)
                else:
                    logger.info("⏰ 暂无新任务，等待下次检查...")
                # 等待下次检查
                time.sleep(self.check_interval)
        except KeyboardInterrupt:
            logger.info("👋 用户中断，停止监听")
        except Exception as e:
            logger.error(f"监听过程异常: {e}")
            raise


def main():
    """主函数"""
    # ================== 配置区域 ==================

    # 1. 数据库监听配置
    CHECK_INTERVAL = 600  # 检查间隔（秒）
    START_TIME = '2026-03-19 10:00:00'

    # 2. 平台并发数配置（同时运行几个平台）
    # 例如：MAX_WORKERS=4 表示 DeepSeek、豆包、元宝 四个平台同时运行
    MAX_WORKERS = 1

    # 3. 每个平台内部的cookie并发数
    # 例如：COOKIE_WORKERS=2 表示每个平台内部同时使用2个cookie文件
    COOKIE_WORKERS = 3

    # 4. 轮次配置（已废弃，改为从数据库 MaxRounds 字段读取）
    # REPEAT_COUNT = 5  # 不再使用

    # 5. 代理配置 (可选)
    # 方式1: 不使用代理
    PROXY_API = None

    # 方式2: 使用固定代理
    def get_fixed_proxy():
        try:
            proxy = get_proxy()
            logger.info(f"✓ 获取到代理: {proxy.get('server', 'unknown')}")
            return proxy
        except Exception as e:
            logger.error(f"获取代理失败: {e}")
            return None

    # PROXY_API = get_fixed_proxy

    # 6. 排除已完成的平台 (可选)
    EXCLUDE_MODELS = []
    # EXCLUDE_MODELS = []  # 不排除任何平台

    # ================== 运行 ==================

    logger.info("=" * 60)
    logger.info("🎯 AI模型并发调度系统启动 (集成数据库监听)")
    logger.info(f"平台并发数: {MAX_WORKERS} (同时运行{MAX_WORKERS}个平台)")
    logger.info(f"Cookie并发数: {COOKIE_WORKERS} (每个平台内部{COOKIE_WORKERS}个cookie并发)")
    logger.info(f"数据库检查间隔: {CHECK_INTERVAL} 秒")
    if EXCLUDE_MODELS:
        logger.info(f"⚠️  排除平台: {', '.join(EXCLUDE_MODELS)}")
    logger.info("=" * 60)

    # 创建集成运行器并启动
    runner = IntegratedRunner(
        check_interval=CHECK_INTERVAL,
        start_time=START_TIME,
        max_workers=MAX_WORKERS,
        cookie_workers=COOKIE_WORKERS,
        proxy_api=PROXY_API,
        exclude_models=EXCLUDE_MODELS,
    )

    runner.start_monitoring()

    logger.info("=" * 60)
    logger.info("👋 程序执行完毕")
    logger.info("=" * 60)


if __name__ == '__main__':
    main()
