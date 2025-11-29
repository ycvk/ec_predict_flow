from app.core.celery_app import celery_app
from celery import Task
from celery.exceptions import Ignore
import pandas as pd
import numpy as np
import os
from app.core.config import settings
from app.services.label_processor import calculate_label_with_filter
from app.services.calculate_indicator import calculate_atr, calculate_RSI, calculate_fast_cti


class CallbackTask(Task):
    def on_success(self, retval, task_id, args, kwargs):
        pass

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        pass


@celery_app.task(bind=True, base=CallbackTask)
def calculate_labels(self, data_file: str, window: int = 29, look_forward: int = 10, label_type: str = 'up', filter_type: str = 'rsi', threshold: float = None):
    """
    计算标签
    :param data_file: 原始数据文件名（不含路径），从raw目录读取
    :param window: 标签计算窗口，默认29
    :param look_forward: 预测未来多少个周期，默认10
    :param label_type: 标签类型，'up'上涨或'down'下跌
    :param filter_type: 过滤类型，'rsi'或'cti'
    :param threshold: 过滤阈值，如果为None则使用默认值
    """
    try:
        self.update_state(state='PROGRESS', meta={'progress': 0, 'status': '正在加载数据...', 'message': '读取pkl文件'})

        # 读取原始数据文件
        data_path = os.path.join(settings.RAW_DATA_DIR, data_file)
        if not os.path.exists(data_path):
            raise Exception(f"数据文件不存在: {data_file}")

        df = pd.read_pickle(data_path)
        self.update_state(state='PROGRESS', meta={'progress': 10, 'status': '数据加载完成', 'message': f'共 {len(df)} 行数据'})

        # 确保有必要的列（至少要有close价格）
        if 'close' not in df.columns:
            raise Exception("特征文件中缺少'close'列，无法计算标签")

        self.update_state(state='PROGRESS', meta={
            'progress': 20,
            'status': '正在计算标签...',
            'message': f'窗口: {window}, 预测周期: {look_forward}, 类型: {label_type}, 过滤: {filter_type}, 阈值: {threshold}'
        })

        # 计算标签
        labels = calculate_label_with_filter(df, window=window, look_forward=look_forward, label_type=label_type, filter_type=filter_type, threshold=threshold)

        # 重要：使用.values来赋值，避免索引不匹配导致的行数翻倍问题
        df['label'] = labels.values

        self.update_state(state='PROGRESS', meta={
            'progress': 60,
            'status': '标签计算完成',
            'message': '正在统计标签信息...'
        })

        # 计算标签统计
        label_stats = {
            'total_samples': int(len(df)),
            'non_nan_labels': int(df['label'].notna().sum()),
            'label_mean': float(df['label'].mean()) if df['label'].notna().sum() > 0 else 0.0,
            'label_std': float(df['label'].std()) if df['label'].notna().sum() > 0 else 0.0,
            'positive_ratio': float((df['label'] > 0.5).sum() / df['label'].notna().sum()) if df['label'].notna().sum() > 0 else 0.0
        }

        self.update_state(state='PROGRESS', meta={
            'progress': 70,
            'status': '正在保存标签文件...',
            'message': f'非空标签数: {label_stats["non_nan_labels"]}'
        })

        # 构建标签文件名
        # 从 data_file 中提取基础名称
        # 例如: ETHUSDT_BINANCE_2024-01-01_00_00_00_2024-12-31_23_59_59.pkl
        # 转换为: ETHUSDT_BINANCE_2024-01-01_00_00_00_2024-12-31_23_59_59_labels_w29_f10.pkl
        base_name = data_file.replace('.pkl', '')

        labels_filename = f"{base_name}_labels_w{window}_f{look_forward}.pkl"
        labels_path = os.path.join(settings.PROCESSED_DATA_DIR, labels_filename)

        # 保存标签文件（包含datetime和label列）
        cols_to_save = []
        if 'datetime' in df.columns:
            cols_to_save.append('datetime')
        elif isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index()
            if 'datetime' not in df.columns:
                df.rename(columns={'index': 'datetime'}, inplace=True)
            cols_to_save.append('datetime')

        cols_to_save.append('label')
        labels_df = df[cols_to_save].copy()

        labels_df.to_pickle(labels_path)

        self.update_state(state='PROGRESS', meta={
            'progress': 90,
            'status': '标签文件保存完成',
            'message': f'文件: {labels_filename}'
        })

        self.update_state(state='PROGRESS', meta={'progress': 100, 'status': '完成！', 'message': '标签计算完成'})

        # 返回结果
        return {
            "status": "success",
            "labels_file": labels_filename,
            "labels_path": labels_path,
            "data_file": data_file,
            "window": int(window),
            "look_forward": int(look_forward),
            "label_stats": label_stats,
            "total_rows": int(len(df)),
            "message": "标签计算成功"
        }

    except Exception as e:
        import traceback
        error_msg = str(e)
        error_trace = traceback.format_exc()

        print(f"[ERROR] 标签计算任务失败: {error_msg}")
        print(f"[ERROR] 错误堆栈:\n{error_trace}")

        self.update_state(state='FAILURE', meta={
            'status': 'error',
            'message': error_msg,
            'traceback': error_trace
        })

        raise Ignore()


@celery_app.task(bind=True, base=CallbackTask)
def calculate_labels_v2(self, data_file: str, look_forward: int = 10, label_type: str = 'up',
                        filter_type: str = 'rsi', threshold: float = None,
                        methods: list = None, buffer_multiplier: float = 0.5,
                        avg_score_threshold: float = 0.0):
    """
    改进版标签计算 - 针对二元期权特性优化，支持多种方法组合

    基于 how_to_improve_label.md 中的三种改进方法：
    1. safety_buffer: 带安全垫的终点判断 (推荐)
    2. average_price: 平均价格法，评估整个周期质量
    3. multi_horizon: 多周期共振，确保快速反弹

    :param data_file: 原始数据文件名（不含路径），从raw目录读取
    :param look_forward: 预测未来多少个周期，默认10
    :param label_type: 标签类型，'up'上涨或'down'下跌
    :param filter_type: 过滤类型，'rsi'或'cti'
    :param threshold: 过滤阈值，如果为None则使用默认值
    :param methods: 标签计算方法列表，可包含 'safety_buffer', 'average_price', 'multi_horizon'
    :param buffer_multiplier: 安全垫倍数，用于 safety_buffer 方法 (默认0.5倍ATR)
    :param avg_score_threshold: 平均分数阈值，用于 average_price 方法
    """
    try:
        if methods is None or len(methods) == 0:
            methods = ['safety_buffer']

        self.update_state(state='PROGRESS', meta={'progress': 0, 'status': '正在加载数据...', 'message': '读取pkl文件'})

        # 读取原始数据文件
        data_path = os.path.join(settings.RAW_DATA_DIR, data_file)
        if not os.path.exists(data_path):
            raise Exception(f"数据文件不存在: {data_file}")

        df = pd.read_pickle(data_path)
        self.update_state(state='PROGRESS', meta={'progress': 10, 'status': '数据加载完成', 'message': f'共 {len(df)} 行数据'})

        # 确保有必要的列
        if 'close' not in df.columns:
            raise Exception("特征文件中缺少'close'列，无法计算标签")

        methods_str = '+'.join(methods)
        self.update_state(state='PROGRESS', meta={
            'progress': 20,
            'status': '正在计算标签...',
            'message': f'方法: {methods_str}, 预测周期: {look_forward}, 类型: {label_type}, 过滤: {filter_type}'
        })

        # 计算组合标签
        labels = calculate_label_combined(
            df, look_forward=look_forward, label_type=label_type,
            filter_type=filter_type, threshold=threshold,
            methods=methods, buffer_multiplier=buffer_multiplier,
            avg_score_threshold=avg_score_threshold
        )

        # 使用.values来赋值，避免索引不匹配
        df['label'] = labels.values

        self.update_state(state='PROGRESS', meta={
            'progress': 60,
            'status': '标签计算完成',
            'message': '正在统计标签信息...'
        })

        # 计算标签统计
        label_stats = {
            'total_samples': int(len(df)),
            'non_nan_labels': int(df['label'].notna().sum()),
            'label_mean': float(df['label'].mean()) if df['label'].notna().sum() > 0 else 0.0,
            'label_std': float(df['label'].std()) if df['label'].notna().sum() > 0 else 0.0,
            'positive_ratio': float((df['label'] > 0.5).sum() / df['label'].notna().sum()) if df['label'].notna().sum() > 0 else 0.0
        }

        self.update_state(state='PROGRESS', meta={
            'progress': 70,
            'status': '正在保存标签文件...',
            'message': f'非空标签数: {label_stats["non_nan_labels"]}'
        })

        # 构建标签文件名
        base_name = data_file.replace('.pkl', '')
        labels_filename = f"{base_name}_labels_v2_{methods_str}_f{look_forward}.pkl"
        labels_path = os.path.join(settings.PROCESSED_DATA_DIR, labels_filename)

        # 保存标签文件（包含datetime和label列）
        cols_to_save = []
        if 'datetime' in df.columns:
            cols_to_save.append('datetime')
        elif isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index()
            if 'datetime' not in df.columns:
                df.rename(columns={'index': 'datetime'}, inplace=True)
            cols_to_save.append('datetime')

        cols_to_save.append('label')
        labels_df = df[cols_to_save].copy()

        labels_df.to_pickle(labels_path)

        self.update_state(state='PROGRESS', meta={
            'progress': 90,
            'status': '标签文件保存完成',
            'message': f'文件: {labels_filename}'
        })

        self.update_state(state='PROGRESS', meta={'progress': 100, 'status': '完成！', 'message': '标签计算完成'})

        # 返回结果
        return {
            "status": "success",
            "labels_file": labels_filename,
            "labels_path": labels_path,
            "data_file": data_file,
            "methods": methods,
            "look_forward": int(look_forward),
            "label_stats": label_stats,
            "total_rows": int(len(df)),
            "message": "标签计算成功 (V2)"
        }

    except Exception as e:
        import traceback
        error_msg = str(e)
        error_trace = traceback.format_exc()

        print(f"[ERROR] 标签计算任务失败 (V2): {error_msg}")
        print(f"[ERROR] 错误堆栈:\n{error_trace}")

        self.update_state(state='FAILURE', meta={
            'status': 'error',
            'message': error_msg,
            'traceback': error_trace
        })

        raise Ignore()


def calculate_label_combined(df, look_forward=10, label_type='up', filter_type='rsi',
                             threshold=None, methods=None, buffer_multiplier=0.5,
                             avg_score_threshold=0.0):
    """
    组合多种标签计算方法

    支持任意组合三种方法，所有方法的条件必须同时满足（AND逻辑）

    :param df: 输入DataFrame
    :param look_forward: 预测周期
    :param label_type: 'up' 或 'down'
    :param filter_type: 'rsi' 或 'cti'
    :param threshold: 过滤阈值
    :param methods: 方法列表，可包含 'safety_buffer', 'average_price', 'multi_horizon'
    :param buffer_multiplier: 安全垫倍数
    :param avg_score_threshold: 平均分数阈值
    :return: pd.Series，标签
    """
    if methods is None or len(methods) == 0:
        methods = ['safety_buffer']

    df_copy = df.copy()

    # 计算ATR（如果需要safety_buffer方法）
    if 'safety_buffer' in methods:
        df_copy['atr'] = calculate_atr(df, period=14)

    # 计算过滤指标
    if filter_type == 'rsi':
        df_copy['filter_indicator'] = calculate_RSI(df, 14)
        if label_type == 'up':
            if threshold is None:
                threshold = 30
            filter_condition = df_copy['filter_indicator'] < threshold
        else:  # down
            if threshold is None:
                threshold = 70
            filter_condition = df_copy['filter_indicator'] > threshold
    else:  # cti
        df_copy['filter_indicator'] = calculate_fast_cti(df)
        if label_type == 'up':
            if threshold is None:
                threshold = -0.5
            filter_condition = df_copy['filter_indicator'] < threshold
        else:  # down
            if threshold is None:
                threshold = 0.5
            filter_condition = df_copy['filter_indicator'] > threshold

    df_copy['Label'] = np.nan

    # 计算中间点位置（如果需要multi_horizon方法）
    mid_point = look_forward // 2

    # 计算标签
    for i in range(len(df_copy)):
        if not filter_condition.iloc[i]:
            continue

        if i + look_forward >= len(df_copy):
            continue

        close_now = df_copy['close'].iloc[i]
        close_future = df_copy['close'].iloc[i + look_forward]

        # 初始化所有方法的结果为True
        all_conditions_met = True

        # 检查每个方法的条件
        for method in methods:
            if method == 'safety_buffer':
                atr_value = df_copy['atr'].iloc[i]
                if pd.isna(atr_value):
                    all_conditions_met = False
                    break

                delta = atr_value * buffer_multiplier
                if label_type == 'up':
                    if not (close_future > close_now + delta):
                        all_conditions_met = False
                        break
                else:  # down
                    if not (close_future < close_now - delta):
                        all_conditions_met = False
                        break

            elif method == 'average_price':
                window_closes = df_copy['close'].iloc[i+1:i+look_forward+1].values
                avg_deviation = np.mean(window_closes - close_now)

                if label_type == 'up':
                    if not (close_future > close_now and avg_deviation > avg_score_threshold):
                        all_conditions_met = False
                        break
                else:  # down
                    if not (close_future < close_now and avg_deviation < -avg_score_threshold):
                        all_conditions_met = False
                        break

            elif method == 'multi_horizon':
                close_mid = df_copy['close'].iloc[i + mid_point]

                if label_type == 'up':
                    if not (close_mid > close_now and close_future > close_now):
                        all_conditions_met = False
                        break
                else:  # down
                    if not (close_mid < close_now and close_future < close_now):
                        all_conditions_met = False
                        break

        # 根据所有条件是否满足来设置标签
        if all_conditions_met:
            df_copy.iloc[i, df_copy.columns.get_loc('Label')] = 1.0
        else:
            df_copy.iloc[i, df_copy.columns.get_loc('Label')] = 0.0

    return df_copy['Label']


def calculate_label_safety_buffer(df, look_forward=10, label_type='up', filter_type='rsi',
                                   threshold=None, buffer_multiplier=0.5):
    """
    方法1: 带安全垫的终点判断

    不仅要求价格上涨，还要求涨幅超过一个安全阈值（基于ATR动态计算）
    这能过滤掉"微弱上涨"的噪声数据，只保留有明显动能的机会

    标签逻辑:
    - Label 1 (稳赢): P_end > P_start + delta (delta = ATR * buffer_multiplier)
    - Label 0 (其他): P_end <= P_start + delta

    :param df: 输入DataFrame
    :param look_forward: 预测周期
    :param label_type: 'up' 或 'down'
    :param filter_type: 'rsi' 或 'cti'
    :param threshold: 过滤阈值
    :param buffer_multiplier: 安全垫倍数（默认0.5倍ATR）
    :return: pd.Series，标签
    """
    df_copy = df.copy()

    # 计算ATR作为动态阈值
    df_copy['atr'] = calculate_atr(df, period=14)

    # 计算过滤指标
    if filter_type == 'rsi':
        df_copy['filter_indicator'] = calculate_RSI(df, 14)
        if label_type == 'up':
            if threshold is None:
                threshold = 30
            filter_condition = df_copy['filter_indicator'] < threshold
        else:  # down
            if threshold is None:
                threshold = 70
            filter_condition = df_copy['filter_indicator'] > threshold
    else:  # cti
        df_copy['filter_indicator'] = calculate_fast_cti(df)
        if label_type == 'up':
            if threshold is None:
                threshold = -0.5
            filter_condition = df_copy['filter_indicator'] < threshold
        else:  # down
            if threshold is None:
                threshold = 0.5
            filter_condition = df_copy['filter_indicator'] > threshold

    df_copy['Label'] = np.nan

    # 计算标签
    for i in range(len(df_copy)):
        if not filter_condition.iloc[i]:
            continue

        if i + look_forward >= len(df_copy):
            continue

        close_now = df_copy['close'].iloc[i]
        close_future = df_copy['close'].iloc[i + look_forward]
        atr_value = df_copy['atr'].iloc[i]

        # 如果ATR为NaN，跳过
        if pd.isna(atr_value):
            continue

        # 计算安全垫
        delta = atr_value * buffer_multiplier

        if label_type == 'up':
            # 要求上涨且超过安全垫
            if close_future > close_now + delta:
                df_copy.iloc[i, df_copy.columns.get_loc('Label')] = 1.0
            else:
                df_copy.iloc[i, df_copy.columns.get_loc('Label')] = 0.0
        else:  # down
            # 要求下跌且超过安全垫
            if close_future < close_now - delta:
                df_copy.iloc[i, df_copy.columns.get_loc('Label')] = 1.0
            else:
                df_copy.iloc[i, df_copy.columns.get_loc('Label')] = 0.0

    return df_copy['Label']


def calculate_label_average_price(df, look_forward=10, label_type='up', filter_type='rsi',
                                   threshold=None, avg_score_threshold=0.0):
    """
    方法2: 平均价格法（积分法）

    评估整个生命周期的质量，计算时间窗内所有价格相对于入场价的平均偏离度
    只有当最终价格满足方向要求，且过程平均分超过阈值时，才标记为1

    Score = (1/N) * Σ(P_i - P_start)

    - 快反弹（V型）: 价格迅速拉升，Score很大
    - 慢反弹（U型）: 前期可能在水下，Score较小甚至为负

    :param df: 输入DataFrame
    :param look_forward: 预测周期
    :param label_type: 'up' 或 'down'
    :param filter_type: 'rsi' 或 'cti'
    :param threshold: 过滤阈值
    :param avg_score_threshold: 平均分数阈值（相对于入场价的平均偏离）
    :return: pd.Series，标签
    """
    df_copy = df.copy()

    # 计算过滤指标
    if filter_type == 'rsi':
        df_copy['filter_indicator'] = calculate_RSI(df, 14)
        if label_type == 'up':
            if threshold is None:
                threshold = 30
            filter_condition = df_copy['filter_indicator'] < threshold
        else:  # down
            if threshold is None:
                threshold = 70
            filter_condition = df_copy['filter_indicator'] > threshold
    else:  # cti
        df_copy['filter_indicator'] = calculate_fast_cti(df)
        if label_type == 'up':
            if threshold is None:
                threshold = -0.5
            filter_condition = df_copy['filter_indicator'] < threshold
        else:  # down
            if threshold is None:
                threshold = 0.5
            filter_condition = df_copy['filter_indicator'] > threshold

    df_copy['Label'] = np.nan

    # 计算标签
    for i in range(len(df_copy)):
        if not filter_condition.iloc[i]:
            continue

        if i + look_forward >= len(df_copy):
            continue

        close_now = df_copy['close'].iloc[i]
        close_future = df_copy['close'].iloc[i + look_forward]

        # 计算窗口内所有价格相对于入场价的平均偏离
        window_closes = df_copy['close'].iloc[i+1:i+look_forward+1].values
        avg_deviation = np.mean(window_closes - close_now)

        if label_type == 'up':
            # 要求: 1) 最终上涨 2) 平均偏离度超过阈值
            if close_future > close_now and avg_deviation > avg_score_threshold:
                df_copy.iloc[i, df_copy.columns.get_loc('Label')] = 1.0
            else:
                df_copy.iloc[i, df_copy.columns.get_loc('Label')] = 0.0
        else:  # down
            # 要求: 1) 最终下跌 2) 平均偏离度低于阈值（负值）
            if close_future < close_now and avg_deviation < -avg_score_threshold:
                df_copy.iloc[i, df_copy.columns.get_loc('Label')] = 1.0
            else:
                df_copy.iloc[i, df_copy.columns.get_loc('Label')] = 0.0

    return df_copy['Label']


def calculate_label_multi_horizon(df, look_forward=10, label_type='up', filter_type='rsi', threshold=None):
    """
    方法3: 多周期共振标签

    要求在T和T/2时刻都满足方向要求，确保是"快速反弹"而非"慢反弹"
    这能剔除那些"时间没卡准"的情况，只保留爆发力强的机会

    标签逻辑:
    - Label 1 (完美形态): P_T/2 > P_start AND P_T > P_start (对于up)
    - Label 0 (其他): 包括慢反弹、V反等

    :param df: 输入DataFrame
    :param look_forward: 预测周期
    :param label_type: 'up' 或 'down'
    :param filter_type: 'rsi' 或 'cti'
    :param threshold: 过滤阈值
    :return: pd.Series，标签
    """
    df_copy = df.copy()

    # 计算过滤指标
    if filter_type == 'rsi':
        df_copy['filter_indicator'] = calculate_RSI(df, 14)
        if label_type == 'up':
            if threshold is None:
                threshold = 30
            filter_condition = df_copy['filter_indicator'] < threshold
        else:  # down
            if threshold is None:
                threshold = 70
            filter_condition = df_copy['filter_indicator'] > threshold
    else:  # cti
        df_copy['filter_indicator'] = calculate_fast_cti(df)
        if label_type == 'up':
            if threshold is None:
                threshold = -0.5
            filter_condition = df_copy['filter_indicator'] < threshold
        else:  # down
            if threshold is None:
                threshold = 0.5
            filter_condition = df_copy['filter_indicator'] > threshold

    df_copy['Label'] = np.nan

    # 计算中间点位置
    mid_point = look_forward // 2

    # 计算标签
    for i in range(len(df_copy)):
        if not filter_condition.iloc[i]:
            continue

        if i + look_forward >= len(df_copy):
            continue

        close_now = df_copy['close'].iloc[i]
        close_mid = df_copy['close'].iloc[i + mid_point]
        close_future = df_copy['close'].iloc[i + look_forward]

        if label_type == 'up':
            # 要求中间点和终点都上涨
            if close_mid > close_now and close_future > close_now:
                df_copy.iloc[i, df_copy.columns.get_loc('Label')] = 1.0
            else:
                df_copy.iloc[i, df_copy.columns.get_loc('Label')] = 0.0
        else:  # down
            # 要求中间点和终点都下跌
            if close_mid < close_now and close_future < close_now:
                df_copy.iloc[i, df_copy.columns.get_loc('Label')] = 1.0
            else:
                df_copy.iloc[i, df_copy.columns.get_loc('Label')] = 0.0

    return df_copy['Label']
