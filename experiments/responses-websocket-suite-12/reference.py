"""Offline exact reference: raw-source calculations and exhaustive schedules."""
import itertools,json
from pathlib import Path
from decimal import Decimal,ROUND_HALF_UP
ROOT=Path(__file__).resolve().parent

def rounded(n,d=1):return int((Decimal(n)/Decimal(d)).quantize(Decimal(1),rounding=ROUND_HALF_UP))
def sources(case):
 p=ROOT/'cases'/case;idx=json.loads((p/'index.json').read_text())
 return idx,{x['id']:json.loads((p/x['path']).read_text()) for x in idx['documents']}
def operations(idx,d):
 policy=d['OPS-POLICY'];cutoff=policy['cutoff_day'];facts={};required={};findings={};diagnostic=[]
 totals={k:0 for k in ['expected','actual','overbilled','underbilled']};known_over=0;known_cancel=0;any_missing=False
 branches=[x['branch'] for x in d.values() if x.get('orders') is not None]
 for branch in branches:
  p=branch.upper();orders=d[p+'-ORDERS']['orders'];ful=d[p+'-FULFILLMENT'];ledger=d[p+'-LEDGER'];complete=ledger['complete']
  ids=['OPS-POLICY',p+'-ORDERS',p+'-FULFILLMENT',p+'-LEDGER'];expected_ids=ids[:3];actual_ids=['OPS-POLICY',p+'-LEDGER']
  values={k:0 for k in totals};counts={k:0 for k in ['overbilled','underbilled','cancelled_billed','duplicate_invoice']}
  for order in orders:
   oid=order['order_id'];qty=sum(x['quantity'] for x in ful['shipments'] if x['order_id']==oid and x['day']<=cutoff)-sum(x['quantity'] for x in ful['returns'] if x['order_id']==oid and x['day']<=cutoff and x['status']=='accepted')
   expected=0 if order['status']=='cancelled' else rounded(qty*order['unit_minor']*(10000-order['discount_basis_points']),10000)
   inv={x['invoice_id']:x for x in ledger['invoices'] if x['order_id']==oid and x['day']<=cutoff and x['status']=='posted'}
   credit={x['credit_id']:x for x in ledger['credits'] if x['order_id']==oid and x['day']<=cutoff and x['status']=='posted' and x['approved']}
   actual=sum(x['amount_minor'] for x in inv.values())-sum(x['amount_minor'] for x in credit.values()) if complete else None
   per={'expected':expected,'actual':actual,'overbilled':max(0,actual-expected) if complete else None,'underbilled':max(0,expected-actual) if complete else None}
   a,b=policy['currency_to_usd'][order['currency']]
   for key,value in per.items():
    if value is not None:values[key]+=value;totals[key]+=rounded(value*a,b)
   if complete:
    counts['overbilled']+=actual>expected;counts['underbilled']+=actual<expected
    counts['cancelled_billed']+=order['status']=='cancelled' and actual>0
    counts['duplicate_invoice']+=expected>0 and actual>expected and sum(x['amount_minor']==expected for x in inv.values())>1
    known_over+=rounded(per['overbilled']*a,b)
   diagnostic.append({'order_id':oid,'net_fulfilled_qty':qty,**per})
  for key,value in values.items():
   name=branch+'.'+key+'_local_minor';facts[name]=value if complete or key=='expected' else None
   required[name]=expected_ids if key=='expected' else actual_ids if key=='actual' or not complete else ids
  for key,value in counts.items():
   name=branch+'.'+key+'_orders';facts[name]=value if complete else None;required[name]=ids if complete else actual_ids
   if value and complete:
    issue='duplicate_invoices' if key=='duplicate_invoice' else key;findings[branch+'.'+issue]=ids
  if complete:known_cancel+=counts['cancelled_billed']
  else:any_missing=True;findings[branch+'.incomplete_ledger']=actual_ids
 for key,value in totals.items():
  name='all.'+key+'_usd_cents';facts[name]=value if key=='expected' or not any_missing else None;required[name]=['OPS-POLICY']
 facts['all.hold_billing']=True if known_cancel or known_over>policy['hold_threshold_usd_cents'] else None if any_missing else False
 required['all.hold_billing']=['OPS-POLICY']
 return facts,required,findings,{'per_order':diagnostic,'known_over_usd':known_over,'known_cancel':known_cancel}

def procurement(idx,d):
 p=d['BUY-POLICY'];demand=d['BUY-DEMAND']['months'];facts={};required={};findings={};costs={};eligible=[]
 vendors=[x['vendor'] for x in d.values() if x.get('status')=='signed' and 'uptime_bps' in x]
 for v in vendors:
  prefix=v.upper();annex=dict(d[prefix+'-ANNEX']);fees=d[prefix+'-FEES'];rules=p['mandatory']
  applied=[]
  for a in sorted(d[prefix+'-AMENDMENTS']['amendments'],key=lambda x:x['effective_day']):
   if a['status']=='executed' and a['effective_day']<=p['cutoff_day']:annex.update(a['fields']);applied.append(a)
  checks={'eu_processing':annex['eu_only'] is True,'uptime':annex['uptime_bps']>=rules['uptime_bps'],
          'capacity':annex['capacity']>=rules['capacity'],'delivery':annex['delivery_weeks']<=rules['delivery_max'],
          'contractual_rto':None if annex['contractual_rto_minutes'] is None else annex['contractual_rto_minutes']<=rules['rto_max']}
  eligibility='ineligible' if any(x is False for x in checks.values()) else 'unknown' if any(x is None for x in checks.values()) else 'eligible'
  ids=['BUY-POLICY',prefix+'-ANNEX',prefix+'-AMENDMENTS'];fid=['BUY-POLICY',prefix+'-FEES'];uid=fid+['BUY-DEMAND']
  for name,value in checks.items():
   facts[v+'.'+name+'_pass']=value;required[v+'.'+name+'_pass']=ids
   if value is not True:findings[v+'.'+name+('.unknown' if value is None else '.fail')]=ids
  hardware=rounded(fees['hardware_cents']*(10000-fees['volume_discount_bps']),10000)
  hardware=rounded(hardware*(10000-fees['promo_discount_bps']),10000)
  setup=sum(fees['integration_cents'][x] for x in p['required_integrations'])+fees['implementation_cents']
  subscription=sum(x['seats']*fees['seat_monthly_cents_by_year'][(x['month']-1)//12] for x in demand)
  support=p['years']*rounded(hardware*fees['support_basis_points'],10000)
  usage=sum(max(0,x['units']-fees['free_units_per_month'])*fees['overage_cents_per_unit'] for x in demand)
  values={'eligibility':eligibility,'hardware_cents':hardware,'setup_cents':setup,'subscription_cents':subscription,'support_cents':support,'usage_cents':usage,'tco_cents':hardware+setup+subscription+support+usage}
  for name,value in values.items():
   facts[v+'.'+name]=value;required[v+'.'+name]=ids if name=='eligibility' else uid if name in ('subscription_cents','usage_cents','tco_cents') else fid
  costs[v]=values['tco_cents']
  if eligibility=='eligible':eligible.append(v)
 selected=min(eligible,key=lambda x:(costs[x],x)) if eligible else None
 facts.update({'all.selected_vendor':selected,'all.selected_tco_cents':costs[selected] if selected else None,'all.eligible_count':len(eligible)})
 for name in list(facts):
  if name.startswith('all.'):required[name]=['BUY-POLICY']
 return facts,required,findings,{'effective_annexes':{v:[a for a in d[v.upper()+'-AMENDMENTS']['amendments'] if a['status']=='executed' and a['effective_day']<=p['cutoff_day']] for v in vendors}}

def task_times(d,hub,start=None):
 prefix=hub.upper();tasks={x['id']:x for x in d[prefix+'-TASKS']['tasks']};dur={x['id']:x['duration'] for x in tasks.values()}
 for change in sorted(d[prefix+'-CHANGES']['entries'],key=lambda x:x['effective_day']):
  if change['status']=='executed':dur.update(change['durations'])
 remaining=set(tasks);finished={};starts={}
 while remaining:
  ready=sorted(x for x in remaining if set(tasks[x]['predecessors'])<=finished.keys())
  if not ready:raise ValueError('Dependency cycle')
  for x in ready:
   a=max([tasks[x]['release_day']]+[finished[y] for y in tasks[x]['predecessors']])
   if x=='inspection' and start is not None:
    if start<a:raise ValueError('Inspection before ready')
    a=start
   starts[x]=a;finished[x]=a+dur[x];remaining.remove(x)
 return starts,finished,dur

def planning(idx,d):
 hubs=d['PLAN-CALENDAR']['hub_order'];facts={};required={};findings={};choices={};candidates=[]
 for hub in hubs:
  p=hub.upper();starts,fin,durations=task_times(d,hub);ids=['PLAN-POLICY',p+'-TASKS',p+'-CHANGES']
  facts[hub+'.earliest_inspection_start']=starts['inspection'];facts[hub+'.unconstrained_completion']=fin['open']
  required[hub+'.earliest_inspection_start']=ids;required[hub+'.unconstrained_completion']=ids
  choices[hub]=[s for s in sorted(set(d['PLAN-CALENDAR']['available_starts'])&set(d[p+'-ACCESS']['available_starts'])) if s>=starts['inspection']]
  baseline=d[p+'-BASELINE'];bid=ids+[p+'-BASELINE']
  if any(baseline['durations'][t]!=v for t,v in durations.items()):findings[hub+'.stale_duration']=bid
  if baseline['inspection_start']<starts['inspection']:findings[hub+'.baseline_precedence_violation']=bid
 for slots in itertools.product(*(choices[h] for h in hubs)):
  if len(set(slots))!=len(slots):continue
  completions=[task_times(d,h,s)[1]['open'] for h,s in zip(hubs,slots)]
  candidates.append((max(completions),sum(completions),slots,tuple(completions)))
 best=min(candidates) if candidates else None
 for i,hub in enumerate(hubs):
  p=hub.upper();ids=['PLAN-POLICY',p+'-TASKS',p+'-CHANGES','PLAN-CALENDAR',p+'-ACCESS']
  start=best[2][i] if best else None;finish=best[3][i] if best else None;slack=d[p+'-TASKS']['deadline']-finish if best else None
  for key,val in {'inspection_start':start,'completion_day':finish,'deadline_slack':slack,'deadline_met':slack>=0 if best else None}.items():facts[hub+'.'+key]=val;required[hub+'.'+key]=ids
  if best and slack<0:findings[hub+'.deadline_miss']=ids
 baseline=[d[h.upper()+'-BASELINE']['inspection_start'] for h in hubs]
 if len(set(baseline))!=len(baseline):findings['shared.baseline_inspection_collision']=['PLAN-POLICY']+[h.upper()+'-BASELINE' for h in hubs]
 if best is None:findings['shared.no_feasible_schedule']=['PLAN-POLICY','PLAN-CALENDAR']+[h.upper()+'-ACCESS' for h in hubs]
 facts.update({'all.makespan':best[0] if best else None,'all.sum_completion_days':best[1] if best else None,'all.open_all_on_time':all(facts[h+'.deadline_met'] for h in hubs) if best else False})
 for name in facts:
  if name.startswith('all.'):required[name]=['PLAN-POLICY','PLAN-CALENDAR']
 return facts,required,findings,{'feasible_schedule_count':len(candidates),'best':best,'choices':choices}

SOLVERS={'operations':operations,'procurement':procurement,'planning':planning}
def solve(case):
 idx,docs=sources(case);facts,required,findings,diagnostic=SOLVERS[idx['domain']](idx,docs)
 assert set(facts)==set(idx['requested_fact_names'])
 return {'facts':facts,'required_sources':required,'findings':sorted(findings),'finding_sources':findings,'diagnostic':diagnostic}
if __name__=='__main__':
 for p in sorted((ROOT/'cases').iterdir()):
  answer=solve(p.name);dest=p/'oracle.json'
  if dest.exists():assert json.loads(dest.read_text())==json.loads(json.dumps(answer))
  else:dest.write_text(json.dumps(answer,indent=2)+'\n')
  print(json.dumps({'case':p.name,'facts':len(answer['facts']),'findings':len(answer['findings']),'feasible_schedules':answer['diagnostic'].get('feasible_schedule_count')}))
