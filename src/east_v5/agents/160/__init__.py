"""160-确定性预审 agent（硬编码审核，无 LLM 裁决权）。"""
from .precheck import PrecheckAgent, REPORT_RULE_ORDER, RULE_LABELS, consume_170_180_stub

__all__ = ["PrecheckAgent", "REPORT_RULE_ORDER", "RULE_LABELS", "consume_170_180_stub"]
