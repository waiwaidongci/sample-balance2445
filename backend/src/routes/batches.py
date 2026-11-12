from fastapi import APIRouter,Depends,Query
from src.config.database import session
from src.middlewares.auth import current_user,require
from src.schemas.batch import BatchInput
from src.schemas.unit import CSVInput
from src.schemas.target import TargetSetInput
from src.services.batch_service import list_batches
from src.services.unit_service import list_units
from src.services.target_service import list_targets
from src.services.feasibility_service import batch_feasibility
from src.controllers import batch_controller,unit_controller,target_controller
router=APIRouter(prefix='/batches',tags=['samples'])
@router.get('')
def listing(db=Depends(session),user=Depends(current_user)):return list_batches(db)
@router.post('',status_code=201)
def create(data:BatchInput,db=Depends(session),user=Depends(require('admin','analyst'))):return batch_controller.create(db,data,user)
@router.get('/{batch_id}/units')
def units(batch_id:int,db=Depends(session),user=Depends(current_user)):return list_units(db,batch_id)
@router.post('/{batch_id}/import',status_code=201)
def importing(batch_id:int,data:CSVInput,db=Depends(session),user=Depends(require('admin','analyst'))):return unit_controller.create(db,batch_id,data,user)
@router.get('/{batch_id}/targets')
def targets(batch_id:int,db=Depends(session),user=Depends(current_user)):return list_targets(db,batch_id)
@router.put('/{batch_id}/targets')
def replacing(batch_id:int,data:TargetSetInput,db=Depends(session),user=Depends(require('admin'))):return target_controller.replace(db,batch_id,data,user)
@router.get('/{batch_id}/feasibility')
def feasibility(batch_id:int,cap_ratio:float=Query(default=8,ge=1,le=30),db=Depends(session),user=Depends(current_user)):
    """运行前联合可行性预检：实时计算，源数据或目标更新后指纹变化、旧诊断失效。"""
    return batch_feasibility(db,batch_id,cap_ratio)
