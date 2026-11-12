export const Role={ADMIN:'admin',ANALYST:'analyst',VIEWER:'viewer'}as const;
export const RunStatus={QUEUED:'queued',RUNNING:'running',COMPLETED:'completed',FAILED:'failed'}as const;
export const Dimension={ZONE:'zone',AGE_BAND:'age_band',MODE:'mode'}as const;
export const dimensionLabels:Record<string,string>={zone:'居住分区',age_band:'年龄组',mode:'出行方式'};
export const statusLabels:Record<string,string>={queued:'等待校准',running:'迭代计算',completed:'计算完成',failed:'需要处理'};
export const feasibilityLabels:Record<string,string>={feasible:'预检可行',cap_insufficient:'权重上限不足',structurally_infeasible:'样本交叉结构无解',invalid_targets:'目标口径待修正'};
export const reasonLabels:Record<string,string>={structurally_infeasible:'样本交叉结构无解',cap_insufficient:'权重上限不足',not_converged:'暂未收敛'};
export const roleLabels:Record<string,string>={admin:'抽样负责人',analyst:'交通分析师',viewer:'研究阅读者'};
