"""Versioned research formulas; source discovery never grants production approval."""
from __future__ import annotations

import ast
import copy
import json
import math
import re
from dataclasses import dataclass
from datetime import date
from pathlib import PurePosixPath

import numpy as np
import pandas as pd

from finahinking.data.panel_contracts import FIELD_DEFINITIONS, units_checked
from finahinking.data.research_panel import ResearchPanel
from finahinking.factors.panel_operators import (
    OPERATOR_CONTRACTS,
    _validate_panel,
    evaluate_panel,
    parse_panel_expression,
)

from .asset_contracts import FactorSpec
from .contracts import stable_digest

SEMANTICS_VERSION = 'factor-library-dsl.v1'
FAMILIES = ('trend_momentum', 'reversal', 'volatility_tail', 'liquidity_turnover', 'value',
            'quality_profitability_accrual', 'growth_investment', 'industry_style', 'event_institutional', 'etf_macro')
_PRIMITIVES = {'add':2, 'subtract':2, 'multiply':2, 'divide':2, 'negate':1, 'abs':1, 'log':1, 'sqrt':1, 'maximum':2, 'minimum':2}
_ID = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}')


@dataclass(frozen=True)
class LibraryExpression:
    expression: str
    fields: tuple[str, ...]
    cost: int


def parse_library_expression(expression, fields, *, row_count=1):
    if not isinstance(expression, str) or not expression or len(expression.encode()) > 4096:
        raise ValueError('因子表达式为空或过长')
    if type(row_count) is not int or not 1 <= row_count <= 10000:
        raise ValueError('因子行数超限')
    try:
        root = ast.parse(expression.strip(), mode='eval').body
    except (SyntaxError, RecursionError) as exc:
        raise ValueError('因子表达式无效') from exc
    if len(list(ast.walk(root))) > 192:
        raise ValueError('因子表达式节点超限')
    names, cost = set(), 0
    def visit(node, depth=0):
        nonlocal cost
        if depth > 16:
            raise ValueError('因子表达式嵌套超限')
        if isinstance(node, ast.Name):
            if node.id not in fields:
                raise ValueError('因子必需字段未声明')
            names.add(node.id)
            return node.id
        if isinstance(node, ast.Constant) and type(node.value) in (int,float):
            try:
                valid = math.isfinite(node.value) and abs(node.value) <= 1e12
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError('因子常数超限')
            return str(node.value)
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.keywords:
            raise ValueError('仅接受白名单函数与字段')
        name = node.func.id
        if name in _PRIMITIVES:
            if len(node.args) != _PRIMITIVES[name]:
                raise ValueError('因子算术参数数量错误')
            cost += row_count
            result = f'{name}({",".join(visit(a,depth+1) for a in node.args)})'
        elif name in OPERATOR_CONTRACTS:
            count = 2 if name in {'ts_corr','ts_cov','cs_neutralize'} or name.startswith('group_') else 1
            parts = [visit(a,depth+1) for a in node.args[:count]]
            placeholders = [f'input{i}' for i in range(count)]
            tail = [ast.unparse(a) for a in node.args[count:]]
            checked = parse_panel_expression(f'{name}({",".join(placeholders+tail)})', placeholders, row_count=row_count)
            cost += checked.cost
            result = f'{name}({",".join(parts+tail)})'
        else:
            raise ValueError('未知因子算子')
        if cost > 5_000_000:
            raise ValueError('因子计算预算超限')
        return result
    canonical = visit(root)
    if not names:
        raise ValueError('因子必须依赖数据字段')
    return LibraryExpression(canonical, tuple(sorted(names)), cost)


def formula_fingerprint(expression, units, scope):
    """Window/minimum variants and direction aliases are one economic definition."""
    try:
        root = ast.parse(expression.strip(), mode='eval').body
    except (SyntaxError, RecursionError, AttributeError) as exc:
        raise ValueError('因子表达式无效') from exc
    names = {n.id for n in ast.walk(root) if isinstance(n, ast.Name)}
    parse_library_expression(expression, names)
    # Only an outer direction alias preserves the economic definition.
    while isinstance(root, ast.Call) and root.func.id == 'negate':
        root = root.args[0]
    def normalize(node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Constant):
            return node.value
        name = node.func.id
        count = 2 if name in {'ts_corr','ts_cov','cs_neutralize'} or name.startswith('group_') else 1
        args = [normalize(a) for a in node.args]
        if name.startswith('ts_'):
            args[count:] = ['WINDOW'] * (len(args)-count)
        elif name in OPERATOR_CONTRACTS:
            args[-1] = 'MINIMUM'
        if name in {'add','multiply'}:
            args.sort(key=lambda v: json.dumps(v,sort_keys=True))
        return [name, *args]
    return stable_digest({'ast':normalize(root),'units':units,'scope':scope,'semantics_version':SEMANTICS_VERSION})


def _unit_dimensions(unit):
    """Canonical dimensions, retaining currencies instead of equating all money."""
    if not isinstance(unit, str) or len(unit) > 512:
        raise ValueError('输出单位无效')
    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) is int and node.value == 1:
            return {}
        if isinstance(node, ast.Name):
            if node.id in {'ratio', 'scalar'}:
                return {}
            if node.id.endswith('_per_share'):
                return {node.id.removesuffix('_per_share'): 1, 'shares': -1}
            legal = {u for definition in FIELD_DEFINITIONS.values() for u in definition[2]} | {'session'}
            if node.id not in legal:
                raise ValueError('输出单位未登记')
            return {node.id: 1}
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mult, ast.Div)):
            left, right = visit(node.left), visit(node.right)
            sign = -1 if isinstance(node.op, ast.Div) else 1
            for key, value in right.items(): left[key] = left.get(key, 0) + sign * value
            return {key:value for key,value in left.items() if value}
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'sqrt' and len(node.args) == 1:
            return {key:value/2 for key,value in visit(node.args[0]).items()}
        raise ValueError('输出单位表达式无效')
    try:
        return visit(ast.parse(unit, mode='eval').body)
    except (SyntaxError, RecursionError) as exc:
        raise ValueError('输出单位无效') from exc


def _series(expression, panel):
    frame, audit = _validate_panel(panel), panel.audit
    parsed = parse_library_expression(expression, frame.columns, row_count=len(frame))
    active = frame.is_session.eq(True) & frame.in_universe.eq(True)
    diagnostics = set()
    def visit(node):
        if isinstance(node, ast.Name):
            return frame[node.id].where(active), audit['units'].get(node.id)
        if isinstance(node, ast.Constant):
            return pd.Series(float(node.value), index=frame.index), 'scalar'
        name = node.func.id
        if name in _PRIMITIVES:
            vals = [visit(a) for a in node.args]
            if any(not pd.api.types.is_numeric_dtype(v) or pd.api.types.is_bool_dtype(v) for v,u in vals):
                raise ValueError('因子算术需要数值字段')
            x, unit = vals[0]
            if name in {'add','subtract','maximum','minimum'}:
                y, other = vals[1]
                if _unit_dimensions(unit) != _unit_dimensions(other) and 'scalar' not in {unit,other}:
                    raise ValueError('加减字段单位不一致')
                if name == 'add': result = x+y
                elif name == 'subtract': result = x-y
                elif name == 'maximum': result = np.maximum(x,y)
                else: result = np.minimum(x,y)
                unit = other if unit=='scalar' else unit
            elif name=='multiply':
                y, other = vals[1]; result=x*y
                unit=other if unit=='scalar' else unit if other=='scalar' else f'({unit})*({other})'
            elif name=='divide':
                y, other = vals[1]
                if y.eq(0).any(): diagnostics.add('ZERO_DENOMINATOR')
                result=x/y.replace(0,np.nan)
                unit='ratio' if _unit_dimensions(unit)==_unit_dimensions(other) else unit if other=='scalar' else f'({unit})/({other})'
            elif name=='negate': result=-x
            elif name=='abs': result=x.abs()
            elif name=='log':
                # The shares exception is explicitly log(x / one share), as
                # used by log(volume + 1); no lot/thousand-share conversion.
                if _unit_dimensions(unit) and unit != 'shares':
                    raise ValueError('对数输入需要无量纲比率或以一股为固定基准的 shares')
                if x.le(0).any(): diagnostics.add('LOG_DOMAIN')
                result=np.log(x.where(x>0)); unit='ratio'
            else:
                if x.lt(0).any(): diagnostics.add('SQRT_DOMAIN')
                result=np.sqrt(x.where(x>=0)); unit=f'sqrt({unit})'
            return result.replace([np.inf,-np.inf],np.nan).where(active),unit
        count=2 if name in {'ts_corr','ts_cov','cs_neutralize'} or name.startswith('group_') else 1
        values=[visit(a) for a in node.args[:count]]
        temp=frame.copy(); temporary_audit=copy.deepcopy(audit)
        refs=[]
        for i,(value,unit) in enumerate(values):
            key=f'libraryinput{i}'; refs.append(key); temp[key]=value; temporary_audit['units'][key]=unit
        call=f'{name}({",".join(refs+[ast.unparse(a) for a in node.args[count:]])})'
        result=evaluate_panel(call,ResearchPanel(temp,temporary_audit,panel.contract,panel.fingerprint))
        diagnostics.update(result.attrs['diagnostics'])
        return result,result.attrs['unit']
    values,unit=visit(ast.parse(parsed.expression,mode='eval').body)
    return values, sorted(diagnostics), unit


class FactorLibrary:
    def __init__(self, catalog=None):
        if catalog is None:
            from .asset_catalog import ResearchAssetCatalog
            catalog=ResearchAssetCatalog.packaged()
        cards=json.loads(catalog.read_text('factor-library-sources',max_bytes=1024*1024))
        formulas=json.loads(catalog.read_text('factor-library-formulas',max_bytes=1024*1024))
        if cards.get('schema_version')!='factor-library-sources.v1' or formulas.get('schema_version')!='factor-library-formulas.v1':
            raise ValueError('因子库版本未知')
        if any(not isinstance(doc.get(key), list) or len(doc[key]) > 1000 for doc,key in ((cards,'cards'),(formulas,'formulas'))):
            raise ValueError('因子目录数量或结构无效')
        self._cards={}; self._entries={}
        knowledge_refs = {ref for entry in formulas['formulas'] for ref in entry.get('knowledge_refs', [])}
        known = {item['item_id'] for item in catalog.knowledge_content()['items']} if knowledge_refs else set()
        if not knowledge_refs <= known:
            raise ValueError('因子知识引用不存在')
        for card in cards['cards']:
            for name in ('card_id','title','source_id','repository','path','license_id','license_path','symbol','read_date'):
                if not isinstance(card.get(name),str) or not card[name].strip() or len(card[name]) > 512:
                    raise ValueError('来源卡缺少有界溯源元数据')
            if not re.fullmatch(r'https://github.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',card['repository']):
                raise ValueError('来源仓库引用无效')
            for name in ('path','license_path'):
                path=PurePosixPath(card[name])
                if path.is_absolute() or '..' in path.parts or '\\' in card[name]:
                    raise ValueError('来源路径必须为仓库相对路径')
            date.fromisoformat(card['read_date'])
            if card.get('adaptation_status') not in {'INDEPENDENT_IMPLEMENTATION','REFERENCE_ONLY'}:
                raise ValueError('来源适配状态无效')
            if card.get('formula_status') == 'EXPLICIT' and not card.get('original_formula'):
                raise ValueError('明确原式需要可核对的公式')
            if not _ID.fullmatch(card['card_id']) or card['card_id'] in self._cards:
                raise ValueError('来源卡标识重复或无效')
            if card['formula_status'] not in {'EXPLICIT','UNRESOLVED'} or card['evidence_status'] not in {'SOURCE_REPORTED','DEFINITION_CHECKED'}:
                raise ValueError('来源状态不得冒充本地证据')
            if not re.fullmatch('[a-f0-9]{40}',card['commit']) or not re.fullmatch('[a-f0-9]{64}',card['sha256']):
                raise ValueError('来源缺少固定提交或文件摘要')
            self._cards[card['card_id']]=copy.deepcopy(card)
        for entry in formulas['formulas']:
            key=entry['factor_id']
            for name in ('source_card_ids','knowledge_refs','required_fields'):
                refs=entry.get(name)
                if not isinstance(refs,list) or len(refs)>32 or any(not isinstance(ref,str) or not _ID.fullmatch(ref) for ref in refs) or len(set(refs))!=len(refs):
                    raise ValueError('因子引用必须为有界且不重复的标识')
            if not _ID.fullmatch(key) or key in self._entries or entry['family'] not in FAMILIES or entry['version']!='1.0.0':
                raise ValueError('因子标识、家族或版本无效')
            parsed=parse_library_expression(entry['formula'],entry['required_fields'])
            units_checked(entry['field_units'])
            if entry.get('scope') not in {'ts','cs','mixed'} or not isinstance(entry.get('units'),str) or not entry['units']:
                raise ValueError('因子范围或输出单位无效')
            if parsed.fields != tuple(sorted(entry['required_fields'])) or set(entry['field_units']) != set(parsed.fields):
                raise ValueError('公式字段与单位声明不一致')
            refs=entry['source_card_ids']
            if not refs or any(ref not in self._cards or self._cards[ref]['formula_status']!='EXPLICIT' for ref in refs):
                raise ValueError('可执行定义需要明确原式来源')
            if not entry.get('counterexample') or not entry.get('direction_scope') or not entry.get('numeric_example'):
                raise ValueError('因子缺少反例、方向范围或数值例子')
            normalized=copy.deepcopy(entry); normalized['formula']=parsed.expression
            normalized['formula_fingerprint']=formula_fingerprint(parsed.expression,entry['units'],entry['scope'])
            normalized['implementation_fingerprint']=stable_digest({'entry':normalized,'semantics':SEMANTICS_VERSION})
            self._entries[key]=normalized
            self.spec(key)

    def cards(self): return tuple(copy.deepcopy(self._cards[k]) for k in sorted(self._cards))
    def entries(self): return tuple(copy.deepcopy(self._entries[k]) for k in sorted(self._entries))
    def get(self, factor_id):
        if factor_id not in self._entries: raise ValueError('没有对应的可执行因子定义')
        return copy.deepcopy(self._entries[factor_id])
    def inventory(self):
        unique={row['formula_fingerprint'] for row in self._entries.values()}
        return {'source_card_count':len(self._cards),'executable_formula_count':len(unique),
                'alias_count':len(self._entries)-len(unique),'family_count':len({r['family'] for r in self._entries.values()}),
                'families':{f:len({r['formula_fingerprint'] for r in self._entries.values() if r['family']==f}) for f in FAMILIES},
                'use_status':'RESEARCH_ONLY','production_write':False}
    def spec(self, factor_id):
        row=self.get(factor_id)
        return FactorSpec(factor_id,row['version'],row['formula'],tuple(row['required_fields']),row['units'],
                          'RESEARCH',row.get('window',5),'daily','available-at-or-before-signal','propagate',
                          f"library:{factor_id}@{row['version']}",row['implementation_fingerprint'],tuple(row['source_card_ids']))
    def evaluate(self, factor, panel):
        spec=factor if isinstance(factor,FactorSpec) else self.spec(factor)
        trusted=self.spec(spec.factor_id)
        if spec.fingerprint!=trusted.fingerprint: raise ValueError('因子版本或实现绑定不一致')
        row=self.get(spec.factor_id); frame=_validate_panel(panel); reasons=[]
        for name,unit in row['field_units'].items():
            if name not in frame or panel.audit['units'].get(name)!=unit:
                reasons.append(f'{name}: 缺字段或单位不一致')
        values=pd.Series(np.nan,index=frame.sort_index().index,dtype=float); diagnostics=[]
        if not reasons:
            values,diagnostics,computed_unit=_series(spec.formula,panel)
            if _unit_dimensions(computed_unit) != _unit_dimensions(row['units']):
                raise ValueError('因子输出单位与公式推导不一致')
        reasons.extend(diagnostics)
        if not values.notna().any(): reasons.append('有效样本不足')
        payload={'schema_version':'factor-library-result.v1','factor_spec':spec.to_dict(),
                 'formula_fingerprint':row['formula_fingerprint'],'panel_fingerprint':panel.fingerprint,
                 'status':'NEEDS_DATA' if reasons or panel.status=='NEEDS_DATA' else 'READY',
                 'scope':row['scope'],'missing_reason':reasons,'evidence_refs':list(row['source_card_ids']),
                 'values':[{'observed_at':t.isoformat(),'instrument':a,'value':None if pd.isna(v) else float(v)} for (t,a),v in values.items()],
                 'unit':row['units'],'production_write':False,'use_status':'RESEARCH_ONLY'}
        return {**payload,'fingerprint':stable_digest(payload)}


def load_factor_library(catalog=None):
    library=FactorLibrary(catalog); seen=set(); result=[]
    for row in library.entries():
        if row['formula_fingerprint'] not in seen:
            result.append(library.spec(row['factor_id'])); seen.add(row['formula_fingerprint'])
    return tuple(result)


def resolve_library_factor(factor_id,version=None,catalog=None):
    spec=FactorLibrary(catalog).spec(factor_id)
    if version is not None and spec.version!=version: raise ValueError('因子版本不可用')
    return spec


def evaluate_library_factor(spec,panel,catalog=None):
    return FactorLibrary(catalog).evaluate(spec,panel)
