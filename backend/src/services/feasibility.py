"""联合可行性预检：在启动有界 raking 之前先回答“这组边际与上限到底能不能解”。

三类结论（status）：
  feasible        样本交叉结构与当前上限都存在可行权重，raking 是否收敛只取决于迭代/容差；
  cap_insufficient 结构本身可行（存在正权重满足全部边际），但当前截断上限把可行域切空，
                  minimum_cap_ratio 给出使可行域非空的最小倍数；
  structurally_infeasible 任何正权重都无法同时满足边际（交叉结构零），
                  contradictions 列出成对维度上由最大流最小割见证的矛盾类别集合。

只依赖标准库：Dinic 最大流处理成对 Hall 条件，两阶段单纯形处理三维联合 LP。
所有口径与 raking_algorithm.validate_targets 保持一致，但不修改任何输入。
"""
import hashlib
import json
from collections import defaultdict

DIMS = ('age_band', 'mode', 'zone')
_EPS = 1e-10
_PIVOT_LIMIT = 200000


# --------------------------------------------------------------------------- #
# Dinic 最大流：成对维度的边际本质是二部图运输问题，Hall 条件即可行性。
# --------------------------------------------------------------------------- #
class _Dinic:
    def __init__(self, n):
        self.g = [[] for _ in range(n)]
        self.level = [-1] * n
        self.it = [0] * n

    def add(self, u, v, cap):
        self.g[u].append([v, cap, len(self.g[v])])
        self.g[v].append([u, 0.0, len(self.g[u]) - 1])

    def _bfs(self, s, t):
        self.level = [-1] * len(self.g)
        self.level[s] = 0
        q = [s]
        for u in q:
            for v, cap, _ in self.g[u]:
                if cap > _EPS and self.level[v] < 0:
                    self.level[v] = self.level[u] + 1
                    q.append(v)
        return self.level[t] >= 0

    def _dfs(self, u, t, pushed):
        if u == t:
            return pushed
        while self.it[u] < len(self.g[u]):
            edge = self.g[u][self.it[u]]
            v, cap, rev = edge
            if cap > _EPS and self.level[v] == self.level[u] + 1:
                d = self._dfs(v, t, min(pushed, cap))
                if d > _EPS:
                    edge[1] -= d
                    self.g[v][rev][1] += d
                    return d
            self.it[u] += 1
        return 0.0

    def max_flow(self, s, t):
        flow = 0.0
        while self._bfs(s, t):
            self.it = [0] * len(self.g)
            while True:
                f = self._dfs(s, t, float('inf'))
                if f <= _EPS:
                    break
                flow += f
        return flow

    def source_side(self, s):
        """最小割后源点可达集合，即 Hall 矛盾的见证。"""
        seen = {s}
        stack = [s]
        while stack:
            u = stack.pop()
            for v, cap, _ in self.g[u]:
                if cap > _EPS and v not in seen:
                    seen.add(v)
                    stack.append(v)
        return seen


def _pairwise_hall(units, margins, dim_a, dim_b, total):
    """检查 (dim_a,dim_b) 联合分布能否同时满足两维的一维边际。

    运输网络：源 -> A类别(容量=目标) -> 样本同时出现的(a,b)格 -> B类别(容量=目标) -> 汇。
    不可行时用最小割源侧节点给出矛盾类别与交集容量。
    """
    cats_a = sorted(margins[dim_a])
    cats_b = sorted(margins[dim_b])
    ia = {c: i for i, c in enumerate(cats_a)}
    ib = {c: i for i, c in enumerate(cats_b)}
    cells = defaultdict(float)
    for u in units:
        cells[(u[dim_a], u[dim_b])] += 1.0
    n_a, n_b = len(cats_a), len(cats_b)
    s = 0
    a0 = 1
    b0 = a0 + n_a
    sink = b0 + n_b
    dinic = _Dinic(sink + 1)
    for c in cats_a:
        dinic.add(s, a0 + ia[c], float(margins[dim_a][c]))
    for c in cats_b:
        dinic.add(b0 + ib[c], sink, float(margins[dim_b][c]))
    for (ca, cb) in cells:
        dinic.add(a0 + ia[ca], b0 + ib[cb], float(total))
    flow = dinic.max_flow(s, sink)
    if flow >= total - 1e-7:
        return None
    reachable = dinic.source_side(s)
    a_side = [cats_a[i] for i in range(n_a) if a0 + i in reachable]
    # 与 A 侧类别相连的 B 类别：它们的目标之和就是交集可承载的上限。
    neighbor_b = set()
    for u in units:
        if u[dim_a] in a_side:
            neighbor_b.add(u[dim_b])
    neighbor_capacity = sum(margins[dim_b][c] for c in neighbor_b)
    demand = sum(margins[dim_a][c] for c in a_side)
    return {
        'dimensions': [dim_a, dim_b],
        'categories_a': a_side,
        'categories_b': sorted(neighbor_b),
        'demand': round(demand, 6),
        'capacity': round(neighbor_capacity, 6),
        'detail': '类别 %s 的目标合计 %s 超过样本中与其交叉的类别 %s 可承载的 %s'
                  % ('、'.join(a_side), _fmt(demand), '、'.join(sorted(neighbor_b)), _fmt(neighbor_capacity)),
    }


def _fmt(x):
    return ('%.4f' % x).rstrip('0').rstrip('.')


# --------------------------------------------------------------------------- #
# 两阶段单纯形（稀疏字典形式）：验证三维联合 LP
#   ∑_{cell i 属于类别 c} w_i = target_c，w_i >= 0。
# 样本按三维取值聚合成 cell，每个 cell 一个变量；每行引入一个人工变量得到
# 初始基，第一阶段最小化人工变量之和。约束矩阵每行只有少量非零项，字典只存
# 非零系数，使 5000 样本量级也能在毫秒~亚秒完成。Bland 规则避免循环。
# --------------------------------------------------------------------------- #
def _simplex_feasible(A, b):
    """等式约束 A x = b, x >= 0 是否可行。A 为稠密二维列表，b 为一维列表。"""
    m = len(b)
    n = len(A[0]) if m else 0
    if m == 0:
        return True
    # 字典：基变量（初始为人工变量 n+r）= const[r] + Σ rows[r][j]·x_j（j 为非基变量）。
    rows = [{j: -A[r][j] for j in range(n) if abs(A[r][j]) > _EPS} for r in range(m)]
    const = [float(b[r]) for r in range(m)]
    basis = [n + r for r in range(m)]   # 每行当前基变量的全局列号
    nonbasic = set(range(n))            # 非基变量列号（人工变量初始在基中）
    # 第一阶段目标 z = Σ 人工变量 = Σ const + Σ_j (Σ_r rows[r][j]) x_j。
    zconst = sum(const)
    zrow = {}
    for r in range(m):
        for j, coef in rows[r].items():
            zrow[j] = zrow.get(j, 0.0) + coef
    pivots = 0
    while True:
        # Bland：选 reduced cost 为负的最小索引原始变量进基；人工变量不得回基。
        entering = -1
        for j in sorted(nonbasic):
            if j < n and zrow.get(j, 0.0) < -1e-9:
                entering = j
                break
        if entering < 0:
            break
        # Ratio test：基变量 = const + coef·x_entering，仅 coef<0 的行会随 x_entering 增大而减小。
        leaving, ratio = -1, float('inf')
        for r in range(m):
            coef = rows[r].get(entering, 0.0)
            if coef < -_EPS:
                rr = -const[r] / coef
                if rr < ratio - _EPS or (abs(rr - ratio) <= _EPS and
                                         (leaving < 0 or basis[r] < basis[leaving])):
                    leaving, ratio = r, rr
        if leaving < 0:
            return True  # 目标无界，人工目标下不应出现，保守判可行。
        # 字典旋转：entering 进基、basis[leaving] 离基。
        inv = -1.0 / rows[leaving][entering]
        leaving_var = basis[leaving]
        new_row = {leaving_var: -inv}   # 离基变量转为非基列，这一列不可遗漏。
        for k, coef in rows[leaving].items():
            if k != entering:
                new_row[k] = inv * coef
        new_const = const[leaving] * inv
        ze = zrow.get(entering, 0.0)
        zconst += ze * new_const
        new_zrow = {leaving_var: ze * new_row[leaving_var]}
        for k in set(zrow) | set(new_row):
            if k == entering or k == leaving_var:
                continue
            new_zrow[k] = zrow.get(k, 0.0) + ze * new_row.get(k, 0.0)
        for r in range(m):
            if r == leaving:
                continue
            ar = rows[r].get(entering, 0.0)
            if abs(ar) <= _EPS:
                continue
            const[r] += ar * new_const
            for k, cj in new_row.items():
                rows[r][k] = rows[r].get(k, 0.0) + ar * cj
            rows[r].pop(entering, None)
        rows[leaving], const[leaving], zrow = new_row, new_const, new_zrow
        nonbasic.discard(entering)
        nonbasic.add(leaving_var)
        basis[leaving] = entering
        pivots += 1
        if pivots > _PIVOT_LIMIT:
            return zconst <= 1e-7
    # 第一阶段最优：人工变量之和为 0 才可行。
    return zconst <= 1e-7


def _joint_lp_feasible(units, margins):
    """三维联合可行性：把样本聚合成 cell 变量，类别目标作为等式约束。"""
    cells = defaultdict(float)
    for u in units:
        cells[(u['zone'], u['age_band'], u['mode'])] += 1.0
    cell_keys = sorted(cells)
    # cell 元组顺序固定为 (zone, age_band, mode)，索引必须与之对应。
    tuple_dims = ('zone', 'age_band', 'mode')
    rows, b = [], []
    for dim in DIMS:
        idx = tuple_dims.index(dim)
        for cat in sorted(margins[dim]):
            row = [1.0 if k[idx] == cat else 0.0 for k in cell_keys]
            rows.append(row)
            b.append(float(margins[dim][cat]))
    return _simplex_feasible(rows, b)


# --------------------------------------------------------------------------- #
# 入口：预检编排与指纹。
# --------------------------------------------------------------------------- #
def data_fingerprint(units, targets):
    """样本（维度取值与初始权重、出行时长）+ 目标边际的稳定指纹；任一变化即令旧诊断失效。"""
    payload = {
        'units': sorted(
            [u['code'], u['zone'], u['age_band'], u['mode'],
             round(float(u['base_weight']), 8), round(float(u['trip_minutes']), 8)]
            for u in units
        ),
        'targets': sorted(
            [str(t['dimension']), t['category'], round(float(t['population']), 8)] for t in targets
        ),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    ).hexdigest()[:16]


def precheck(units, targets, cap_ratio):
    """返回联合可行性诊断字典。targets 已通过 raking_algorithm.validate_targets 的口径校验。"""
    margins = defaultdict(dict)
    for t in targets:
        margins[str(t['dimension'])][t['category']] = float(t['population'])
    total = sum(margins[DIMS[0]].values())
    n = len(units)
    avg_weight = total / n

    # 1) 成对 Hall 检查：直接产出可解释的矛盾类别。
    contradictions = []
    for i in range(len(DIMS)):
        for j in range(i + 1, len(DIMS)):
            witness = _pairwise_hall(units, margins, DIMS[i], DIMS[j], total)
            if witness:
                contradictions.append(witness)

    # 2) 三维联合 LP：捕获成对条件都满足、三向仍冲突的更深层结构零。
    joint_feasible = _joint_lp_feasible(units, margins)
    if contradictions or not joint_feasible:
        if not contradictions:
            contradictions.append({
                'dimensions': list(DIMS),
                'categories_a': [],
                'categories_b': [],
                'demand': round(total, 6),
                'capacity': round(total, 6),
                'detail': '三个维度的两两交叉均可运输，但三维联合 LP 无基可行解；'
                          '请放宽至少一个维度的边际类别目标。',
            })
        return {
            'status': 'structurally_infeasible',
            'feasible': False,
            'contradictions': contradictions,
            'minimum_cap_ratio': None,
            'current_cap_ratio': float(cap_ratio),
            'binding_categories': [],
            'fingerprint': data_fingerprint(units, targets),
            'summary': '样本交叉结构无解：不存在满足全部边际的非负权重，提高上限或增加迭代均无效。',
        }

    # 3) 结构可行：逐类别计算最小可行倍数（联合必要下界，取最大者即全局最小倍数）。
    binding = []
    minimum_ratio = 0.0
    for dim in DIMS:
        for cat, target in sorted(margins[dim].items()):
            support = sum(1 for u in units if u[dim] == cat)
            if support <= 0 or target <= 0:
                continue
            need = target / support
            ratio = need / avg_weight
            binding.append({
                'dimension': dim,
                'category': cat,
                'support_count': support,
                'required_weight': round(need, 6),
                'required_ratio': round(ratio, 6),
            })
            minimum_ratio = max(minimum_ratio, ratio)
    minimum_ratio = max(minimum_ratio, 0.0)
    enough = cap_ratio + 1e-9 >= minimum_ratio
    return {
        'status': 'feasible' if enough else 'cap_insufficient',
        'feasible': enough,
        'contradictions': [],
        'minimum_cap_ratio': round(minimum_ratio, 6),
        'current_cap_ratio': float(cap_ratio),
        'binding_categories': sorted(binding, key=lambda x: -x['required_ratio'])[:8],
        'fingerprint': data_fingerprint(units, targets),
        'summary': (
            '结构与上限均可行；若仍未达标，属于暂未收敛，可增加迭代次数或收紧容差。'
            if enough else
            '结构可行但权重上限不足：截断倍数至少提高到 %s（当前 %s）。'
            % (_fmt(minimum_ratio), _fmt(cap_ratio))
        ),
    }
