"""批次级联合可行性预检服务：在校准运行之前给出结构/上限诊断。

诊断不持久化、不缓存——每次请求都基于当前样本与目标实时计算，并返回数据指纹；
源数据（样本维度取值）或目标边际一旦更新，指纹立即变化，旧诊断自然失效。
"""
from fastapi import HTTPException
from .batch_service import get_batch
from .unit_service import list_units
from .target_service import list_targets
from .raking_algorithm import validate_targets
from .feasibility import precheck, data_fingerprint


def batch_feasibility(db, batch_id, cap_ratio):
    get_batch(db, batch_id)
    units = list_units(db, batch_id)
    target_rows = list_targets(db, batch_id)
    if not target_rows:
        raise HTTPException(422, '请先登记边际目标，再做可行性预检')
    targets = [{k: t[k] for k in ('dimension', 'category', 'population')} for t in target_rows]
    try:
        validate_targets(units, targets)
    except ValueError as exc:
        # 口径不合法时不做 LP，直接把校验问题返回，页面可据此修正目标。
        return {
            'status': 'invalid_targets',
            'feasible': False,
            'contradictions': [],
            'minimum_cap_ratio': None,
            'current_cap_ratio': float(cap_ratio),
            'binding_categories': [],
            'fingerprint': data_fingerprint(units, targets),
            'summary': str(exc),
        }
    result = precheck(units, targets, cap_ratio)
    result['batch_id'] = batch_id
    result['sample_count'] = len(units)
    result['target_count'] = len(targets)
    return result
