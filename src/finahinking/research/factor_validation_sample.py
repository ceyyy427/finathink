"""Versioned synthetic observations: deterministic integers, never market evidence."""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from finahinking.data.panel_contracts import FIELD_DEFINITIONS, PanelContract, PanelFieldAttachment
from finahinking.data.research_panel import ResearchPanel, build_research_panel
from finahinking.p8_2.contracts import DatasetSnapshot, MarketObservation

from .contracts import stable_digest

SAMPLE_SPEC = {'schema_version':'factor-validation-sample.v1','seed':2501,'assets':64,'observations':756,
               'start':'2021-01-04','calendar':'business-day observations; synthetic, no exchange calendar',
               'generator':'integer-modular-v2','data_mode':'SAMPLE','price_basis':'RAW','variant':'baseline'}


class ValidationSample:
    def __init__(self, spec=None):
        self.spec = dict(SAMPLE_SPEC if spec is None else spec)
        if (set(self.spec) != set(SAMPLE_SPEC) or self.spec['schema_version'] != SAMPLE_SPEC['schema_version']
                or self.spec['generator'] != SAMPLE_SPEC['generator'] or self.spec['data_mode'] != 'SAMPLE'
                or type(self.spec['assets']) is not int or not 4 <= self.spec['assets'] <= 64
                or type(self.spec['observations']) is not int or not 60 <= self.spec['observations'] <= 756
                or any(self.spec[k]!=SAMPLE_SPEC[k] for k in ('seed','start','calendar','price_basis'))
                or self.spec['variant'] not in {'baseline','suspension','delisting','extreme_return','late_announcement','coverage_gap'}):
            raise ValueError('未知或超限的合成样例规范')
        self.times=pd.bdate_range(self.spec['start'],periods=self.spec['observations'],tz='UTC')
        self.assets=tuple(f'SAMPLE{i:03}' for i in range(self.spec['assets']))
        self._t=np.arange(len(self.times))[:,None];self._a=np.arange(len(self.assets))[None,:]
        from .factor_library import FactorLibrary
        units={'open':'CNY','high':'CNY','low':'CNY','close':'CNY','amount':'CNY','volume':'shares'}
        for row in FactorLibrary().entries():units.update(row['field_units'])
        hashes={}
        for name in sorted(units):
            values=self.field(name).to_numpy()
            raw=values.astype('<f8').tobytes() if name!='industry' else '\n'.join(values.flatten()).encode()
            hashes[name]=hashlib.sha256(raw).hexdigest()
        self.content_digest=stable_digest({'field_hashes':hashes,'field_units':units,
            'times':[t.isoformat() for t in self.times],'assets':self.assets,
            'availability_rules':{'default':'at observation','variant':self.spec['variant']}})
        self.fingerprint=stable_digest({'spec':self.spec,'content_digest':self.content_digest})

    def field(self, name):
        t,a=self._t,self._a
        shock=((t*37+a*53+self.spec['seed'])%97-48)/1000
        close=30+a*.2+t*.012+((t*13+a*17)%31)/15
        if self.spec['variant']=='extreme_return':close=close*np.where((t==len(self.times)//2)&(a==0),.25,1.)
        if name=='close':values=close
        elif name=='open':values=close*(1-shock/4)
        elif name=='high':values=np.maximum(close,close*(1-shock/4))+.3
        elif name=='low':values=np.minimum(close,close*(1-shock/4))-.3
        elif name=='volume':values=10000+(t*257+a*503)%30000
        elif name=='amount':values=self.field('volume').to_numpy()*close
        elif name=='industry':values=np.broadcast_to(np.array([f'sector-{i%4}' for i in range(len(self.assets))]),close.shape)
        elif name in {'strategy_return','benchmark_return'}:values=((t*(31 if name=='strategy_return' else 29)+a*47)%101-50)/2000
        elif name=='total_return':values=np.vstack([np.zeros((1,len(self.assets))),close[1:]/close[:-1]-1])+0.0001
        elif name=='turnover_rate':values=.01+((t*19+a*7)%100)/10000
        elif name in {'bond_yield_long','bond_yield_short'}:values=.02+(.01 if name.endswith('long') else 0)+((t//21+a)%20)/10000
        elif name=='market_cap':values=close*(1000000+a*10000+t*100)
        else:
            key=int(hashlib.sha256(name.encode()).hexdigest()[:8],16)%1000
            values=(100000+key*100+a*101+(t//21)*311+((t//21+a*7+key)%19)*101).astype(float)
        return pd.DataFrame(values,index=self.times,columns=self.assets)

    def anchor(self):
        close=self.field('close').iloc[:,0]
        return DatasetSnapshot('phase25-fixed-sample-anchor',tuple(MarketObservation(self.assets[0],t,c,c+.3,c-.3,c,10000,available_at=t,suspended=False) for t,c in close.items()),provenance={'adjustment':'RAW','sample_spec':self.spec,'sample_fingerprint':self.fingerprint})

    def reference_panel(self, entry, start, stop):
        """Full same-date cross-section; snapshot and attachments share bounded shard."""
        times=self.times[start:stop]
        if not 1 <= len(times) <= 36:raise ValueError('合成面板分片日期超限')
        native={'open','high','low','close','volume','amount'}
        fields={k:self.field(k).iloc[start:stop] for k in native|set(entry['required_fields'])}
        observations=[];records=[]
        rules={'is_session':'boolean','in_universe':'boolean','trade_allowed':'boolean','lot_size':'shares','settlement_days':'sessions','split_factor':'ratio'}
        units={**rules,**{k:v for k,v in entry['field_units'].items() if k not in native}}
        for asset in self.assets:
            for t in times:
                observations.append(MarketObservation(asset,t,**{k:float(fields[k].at[t,asset]) for k in native},available_at=t,suspended=bool(self.spec['variant']=='suspension' and (self.times.get_loc(t)+self.assets.index(asset))%17==0)))
                present=self.spec['variant']!='delisting' or self.assets.index(asset)<len(self.assets)-2 or self.times.get_loc(t)<len(self.times)*.7
                records.append({'instrument':asset,'timestamp':t.isoformat(),'available_at':t.isoformat(),
                    'is_session':True,'in_universe':present,'trade_allowed':True,'lot_size':1,'settlement_days':0,'split_factor':1})
                if self.spec['variant']=='coverage_gap' and self.assets.index(asset)%3==0:continue
                fields_at=t+pd.Timedelta(days=10) if self.spec['variant']=='late_announcement' else t
                records.append({'instrument':asset,'timestamp':t.isoformat(),'available_at':fields_at.isoformat(),
                    **{k:fields[k].at[t,asset] for k in entry['required_fields'] if k not in native}})
        snapshot=DatasetSnapshot('phase25-synthetic-shard',tuple(observations),provenance={'adjustment':'RAW'})
        attachment=PanelFieldAttachment(snapshot.fingerprint,units,records,'Synthetic fixed sample; no user-market evidence')
        contract=PanelContract(snapshot_ref=snapshot.fingerprint,instrument_ids=self.assets,observed_at=[t.isoformat() for t in times],
            field_units={**{k:('shares' if k=='volume' else 'CNY') for k in native},**entry['field_units']},scope_id='phase25-synthetic',
            currencies={a:'CNY' for a in self.assets},timezones={a:'UTC' for a in self.assets},calendar_ref=attachment.fingerprint,
            universe_ref=attachment.fingerprint,adjustment_ref=attachment.fingerprint,trade_rule_ref=attachment.fingerprint)
        return build_research_panel(snapshot,[attachment],contract)

    def panel(self,entry,start,stop):
        """Bounded SAMPLE-only vector projection, checked against the general PIT adapter.

        Values and availability are generated locally by this immutable specification.
        User attachments always use build_research_panel, never this fast path.
        """
        times=self.times[start:stop]
        if not 0<=start<stop<=len(self.times) or len(times)>36:raise ValueError('合成分片边界无效')
        native={'open','high','low','close','volume','amount'}
        units={**{k:'shares' if k=='volume' else 'CNY' for k in native},**entry['field_units'],
            'is_session':'boolean','in_universe':'boolean','trade_allowed':'boolean','lot_size':'shares',
            'settlement_days':'sessions','split_factor':'ratio'}
        index=pd.MultiIndex.from_product([times,self.assets],names=['observed_at','instrument'])
        if len(index)>10000 or len(index)*(len(units)+2)>50000:raise ValueError('合成分片计算预算超限')
        frame=pd.DataFrame(index=index);reasons=[];availability={}
        for name in native|set(entry['required_fields']):
            values=self.field(name).copy()
            if name not in native:
                if self.spec['variant']=='coverage_gap':values.iloc[:,::3]=None if name=='industry' else float('nan')
                if self.spec['variant']=='late_announcement':
                    lag=self.times.searchsorted(self.times-pd.Timedelta(days=10),side='right')-1
                    if FIELD_DEFINITIONS[name][3]=='exact':values.iloc[:,:]=None if name=='industry' else float('nan')
                    else:
                        values=values.iloc[np.maximum(lag,0)].set_axis(self.times)
                        values.loc[self.times[lag<start],:]=None if name=='industry' else float('nan')
                    availability[name]='source observation <= decision - 10 calendar days'
            frame[name]=values.iloc[start:stop].to_numpy().flatten()
        for name,value in {'is_session':True,'in_universe':True,'trade_allowed':True,'lot_size':1.,'settlement_days':0.,'split_factor':1.,'suspended':False}.items():frame[name]=value
        if self.spec['variant']=='delisting':
            mask=np.array([[self.assets.index(a)>=len(self.assets)-2 and self.times.get_loc(t)>=len(self.times)*.7 for a in self.assets] for t in times])
            frame['in_universe']=~mask.flatten()
        if self.spec['variant']=='suspension':frame['suspended']=[(self.times.get_loc(t)+self.assets.index(a))%17==0 for t,a in index]
        missing=frame[list(entry['required_fields'])].isna().any(axis=1)
        for time,asset in index[missing]:
            for name in entry['required_fields']:
                if pd.isna(frame.at[(time,asset),name]):reasons.append(f'missing_field：{name} 在此时点不可用')
        frame['execution_eligible']=~missing & frame['in_universe'] & ~frame['suspended']
        source_ref=stable_digest({'sample':self.fingerprint,'start':start,'stop':stop,'fields':entry['field_units']})
        contract=PanelContract(snapshot_ref=source_ref,instrument_ids=self.assets,observed_at=[t.isoformat() for t in times],
            field_units={k:v for k,v in units.items() if k in native|set(entry['required_fields'])},scope_id='phase25-synthetic',
            currencies={a:'CNY' for a in self.assets},timezones={a:'UTC' for a in self.assets},
            calendar_ref=source_ref,universe_ref=source_ref,adjustment_ref=source_ref,trade_rule_ref=source_ref)
        audit={'status':'NEEDS_DATA' if reasons else 'READY','reasons':sorted(set(reasons)),'units':units,
            'coverage':{k:int(frame[k].notna().sum()) for k in units},'price_basis':'raw','attachment_refs':[source_ref],
            'rule':'SAMPLE only: observed_at <= decision and available_at <= decision; no backfill',
            'projection_version':'synthetic-availability-projection.v1','availability':availability,
            'input_content_digest':self.content_digest,'row_count':len(index),'cell_count':frame.size}
        return ResearchPanel(frame,audit,contract,stable_digest({'contract':contract.fingerprint,'audit':audit}))
