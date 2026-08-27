# -*- coding: utf-8 -*-
"""EAST V5 审阅工作簿冻结结构规格（单一事实源，生成器与校验器共享）。

本模块只承载 Sol 冻结的结构常量，不含任何真实 S0 / 真实 SQL / 真实模型数据。
所有内容均为脱敏 synthetic fixture 的结构定义。
"""

# --- 工作表（冻结顺序） ---
SHEET_OVERVIEW = "审阅总览"
SHEET_DETAIL = "30候选明细"
SHEET_EVIDENCE = "运行与校验证据"
NUM_QUESTIONS = 5

# Q1..Q5 工作表名
def q_sheet_name(i: int) -> str:
    assert 1 <= i <= NUM_QUESTIONS
    return "Q%d" % i

SHEET_ORDER = (
    [SHEET_OVERVIEW, SHEET_DETAIL]
    + [q_sheet_name(i) for i in range(1, NUM_QUESTIONS + 1)]
    + [SHEET_EVIDENCE]
)

# --- 六路 baseline（冻结） ---
BASELINES = [
    "DeepEye-SQL",
    "DataGallery-Text2SQL",
    "JoyDataAgent-SQL",
    "Databao Agent",
    "ReFoRCE",
    "AutoLink",
]
NUM_BASELINES = len(BASELINES)
TOTAL_SLOTS = NUM_QUESTIONS * NUM_BASELINES  # 30

# --- 30候选明细 列头（冻结，19 列 A..S） ---
DETAIL_HEADERS = [
    "题序",        # A
    "qa_id",       # B
    "完整问题",    # C
    "baseline",    # D
    "原始SQL",     # E
    "规范化SQL",   # F
    "状态",        # G
    "失败码",      # H
    "SQL哈希",     # I
    "模型版本",    # J
    "adapter版本", # K
    "upstream版本",# L
    "attempt",     # M
    "call",        # N
    "token",       # O
    "latency",     # P
    "trace引用",   # Q
    "人工结论",    # R
    "备注",        # S
]
# 问题/SQL 纯文本列（公式注入防护对象，1 起索引）
DETAIL_TEXT_COLS = [3, 5, 6]  # C=完整问题, E=原始SQL, F=规范化SQL
DETAIL_STATUS_COL = 7         # G=状态
DETAIL_CONCLUSION_COL = 18    # R=人工结论

# --- Q1..Q5 表内布局（冻结） ---
# 行 1: qa_id | 值
# 行 2: 题序 | 值
# 行 3: 完整问题 | 值(合并 B3:F3)
# 行 4: 空
# 行 5: 表头  baseline|原始SQL|规范化SQL|状态|失败码|SQL哈希
# 行 6..11: 6 路 baseline
Q_HEADERS = ["baseline", "原始SQL", "规范化SQL", "状态", "失败码", "SQL哈希"]
Q_HEADER_ROW = 5
Q_FIRST_DATA_ROW = 6
Q_LAST_DATA_ROW = Q_FIRST_DATA_ROW + NUM_BASELINES - 1  # 11
Q_TEXT_COLS = [2, 3]  # B=原始SQL, C=规范化SQL
Q_STATUS_COL = 4      # D=状态

# --- 受限下拉值（冻结） ---
REVIEW_CONCLUSION_VALUES = ["通过", "不通过", "需复核", "待审阅"]
STATUS_VALUES = ["成功", "失败", "跳过", "待定"]

# --- 公式注入风险前缀 ---
FORMULA_RISK_PREFIXES = ["=", "+", "-", "@"]

# --- 脱敏 synthetic 标记 ---
SYNTH_MARK = "[合成示例]"
