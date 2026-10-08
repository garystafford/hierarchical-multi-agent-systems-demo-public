"""Offline answer-key cross-checks, grading calibration and tool isolation."""
import copy,itertools,json,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import reference,grade,ws_runner,protocol
from report import Report
ROOT=Path(__file__).resolve().parent

def half(n,d=1):return (2*n+d)//(2*d)
def perfect(case):
 a=json.loads((ROOT/'cases'/case/'oracle.json').read_text())
 return {'case_id':case,'status':'completed','output':{'case_id':case,'conclusion':'Reference check',
 'facts':[{'name':k,'value':v,'evidence_ids':a['required_sources'][k],'derivation':'Computed from cited authoritative sources.'} for k,v in a['facts'].items()],
 'findings':[{'key':k,'evidence_ids':a['finding_sources'][k],'explanation':'Supported source finding.'} for k in a['findings']],
 'recommendation':'Use the stated decision.','limitations':['Synthetic source pack.']}}
class GoldenChecks(unittest.TestCase):
 def test_all_twelve_oracles_and_perfect_reports(self):
  cases=sorted((ROOT/'cases').iterdir());self.assertEqual(len(cases),12)
  for p in cases:
   idx,docs=reference.sources(p.name);a=json.loads((p/'oracle.json').read_text())
   self.assertEqual(a,json.loads(json.dumps(reference.solve(p.name))))
   self.assertEqual(set(a['facts']),set(idx['requested_fact_names']))
   report=perfect(p.name);Report.model_validate(report['output']);q=grade.grade(report)
   for key in ['factual_accuracy_pct','finding_f1_pct','required_fact_source_coverage_pct','required_finding_source_coverage_pct']:self.assertEqual(q[key],100,(p.name,key))
   self.assertTrue(q['critical_decision_correct'])
   self.assertTrue(all(set(ids)<=set(docs) for ids in a['required_sources'].values()))
 def test_operations_independent_integer_replay(self):
  for profile in ['compact','independent','large','uncertain']:
   case='operations-'+profile;idx,d=reference.sources(case);a=reference.solve(case);policy=d['OPS-POLICY'];cut=policy['cutoff_day']
   expected_usd=0;actual_usd=0;missing=False;known_over=0;cancel=0
   for v in d.values():
    if 'orders' not in v:continue
    branch=v['branch'];prefix=branch.upper();ful=d[prefix+'-FULFILLMENT'];ledger=d[prefix+'-LEDGER'];exp=act=over=under=0
    for row in v['orders']:
     oid=row['order_id'];quantity=sum(x['quantity'] for x in ful['shipments'] if x['order_id']==oid and x['day']<=cut)
     quantity-=sum(x['quantity'] for x in ful['returns'] if x['order_id']==oid and x['day']<=cut and x['status']=='accepted')
     value=0 if row['status']=='cancelled' else half(quantity*row['unit_minor']*(10000-row['discount_basis_points']),10000)
     exp+=value;n,den=policy['currency_to_usd'][row['currency']];expected_usd+=half(value*n,den)
     if not ledger['complete']:missing=True;continue
     ids=set();posted=0
     for x in ledger['invoices']:
      if x['order_id']==oid and x['invoice_id'] not in ids and x['status']=='posted' and x['day']<=cut:posted+=x['amount_minor'];ids.add(x['invoice_id'])
     ids=set()
     for x in ledger['credits']:
      if x['order_id']==oid and x['credit_id'] not in ids and x['approved'] and x['status']=='posted' and x['day']<=cut:posted-=x['amount_minor'];ids.add(x['credit_id'])
     act+=posted;over+=max(0,posted-value);under+=max(0,value-posted);actual_usd+=half(posted*n,den);known_over+=half(max(0,posted-value)*n,den)
     cancel+=row['status']=='cancelled' and posted>0
    self.assertEqual(a['facts'][branch+'.expected_local_minor'],exp)
    if ledger['complete']:
     self.assertEqual(a['facts'][branch+'.actual_local_minor'],act)
     self.assertEqual(act-exp,over-under)
    else:self.assertIsNone(a['facts'][branch+'.actual_local_minor'])
   self.assertEqual(a['facts']['all.expected_usd_cents'],expected_usd)
   self.assertEqual(a['facts']['all.actual_usd_cents'],None if missing else actual_usd)
   self.assertEqual(a['facts']['all.hold_billing'],True if cancel or known_over>policy['hold_threshold_usd_cents'] else None if missing else False)
  self.assertFalse(reference.solve('operations-compact')['facts']['all.hold_billing'])
 def test_procurement_integer_cost_replay_and_unknowns(self):
  for profile in ['compact','independent','large','uncertain']:
   case='procurement-'+profile;idx,d=reference.sources(case);a=reference.solve(case);p=d['BUY-POLICY'];demand=d['BUY-DEMAND']['months'];eligible=[]
   for row in d.values():
    if 'seat_monthly_cents_by_year' not in row:continue
    v=row['vendor'];hw=half(row['hardware_cents']*(10000-row['volume_discount_bps']),10000);hw=half(hw*(10000-row['promo_discount_bps']),10000)
    setup=row['implementation_cents']+row['integration_cents']['ERP']+row['integration_cents']['WMS']
    sub=0;usage=0
    for month in demand:sub+=month['seats']*row['seat_monthly_cents_by_year'][(month['month']-1)//12];usage+=max(0,month['units']-row['free_units_per_month'])*row['overage_cents_per_unit']
    support=p['years']*half(hw*row['support_basis_points'],10000)
    self.assertEqual(a['facts'][v+'.tco_cents'],hw+setup+sub+usage+support)
    if a['facts'][v+'.eligibility']=='eligible':eligible.append(v)
   winner=min(eligible,key=lambda v:(a['facts'][v+'.tco_cents'],v)) if eligible else None
   self.assertEqual(a['facts']['all.selected_vendor'],winner)
  a=reference.solve('procurement-uncertain');self.assertIsNone(a['facts']['all.selected_vendor']);self.assertEqual(a['facts']['all.eligible_count'],0)
 def test_planning_independent_dfs_and_assignment_enumeration(self):
  for profile in ['compact','independent','large','uncertain']:
   case='planning-'+profile;idx,d=reference.sources(case);a=reference.solve(case);hubs=d['PLAN-CALENDAR']['hub_order'];ready={};tails={};windows={}
   for h in hubs:
    p=h.upper();tasks={t['id']:t for t in d[p+'-TASKS']['tasks']};dur={t['id']:t['duration'] for t in tasks.values()}
    changes=sorted(d[p+'-CHANGES']['entries'],key=lambda x:x['effective_day'])
    for x in changes:
     if x['status']=='executed':dur.update(x['durations'])
    cache={}
    def finish(t):
     if t not in cache:cache[t]=max([tasks[t]['release_day']]+[finish(x) for x in tasks[t]['predecessors']])+dur[t]
     return cache[t]
    ready[h]=finish('inspection')-1;tails[h]=finish('open')-ready[h]
    self.assertEqual(a['facts'][h+'.earliest_inspection_start'],ready[h]);self.assertEqual(a['facts'][h+'.unconstrained_completion'],finish('open'))
    windows[h]=set(d[p+'-ACCESS']['available_starts'])
   candidates=[]
   for slots in itertools.permutations(d['PLAN-CALENDAR']['available_starts'],len(hubs)):
    if all(s>=ready[h] and s in windows[h] for h,s in zip(hubs,slots)):
     ends=[s+tails[h] for h,s in zip(hubs,slots)];candidates.append((max(ends),sum(ends),slots))
   if candidates:
    best=min(candidates);self.assertEqual(a['facts']['all.makespan'],best[0]);self.assertEqual(a['facts']['all.sum_completion_days'],best[1]);self.assertEqual(tuple(a['facts'][h+'.inspection_start'] for h in hubs),best[2])
   else:self.assertIsNone(a['facts']['all.makespan']);self.assertIn('shared.no_feasible_schedule',a['findings'])
 def test_scoring_detects_errors_omissions_and_false_findings(self):
  r=perfect('procurement-uncertain');r['output']['facts'][0]['value']='unsupported';r['output']['facts'][0]['evidence_ids']=['FAKE']
  r['output']['findings'].append({'key':'false.positive','evidence_ids':[],'explanation':'Unsupported'})
  q=grade.grade(r);self.assertLess(q['factual_accuracy_pct'],100);self.assertLess(q['finding_precision_pct'],100);self.assertEqual(q['invalid_source_ids'],['FAKE'])
  r=perfect('planning-compact');r['output']['facts'].append(copy.deepcopy(r['output']['facts'][0]));q=grade.grade(r);self.assertTrue(q['duplicate_facts'])
 def test_tool_cannot_read_oracle_or_arbitrary_paths(self):
  ws_runner.configure('operations-compact')
  for ident in ['oracle.json','../.env','OPS-MISSING']:
   with self.assertRaises(ValueError):protocol.execute_tool({'name':'read_evidence','arguments':json.dumps({'document_ids':[ident]})})
  with self.assertRaises(ValueError):protocol.execute_tool({'name':'execute_code','arguments':'{}'})
 def test_root_collector_excludes_child_and_retains_earlier_report(self):
  c=ws_runner.Collector(True);r=perfect('planning-compact')['output']
  message={'id':'root1','type':'message','status':'completed','phase':'final_answer','agent':{'agent_name':'/root'},'content':[{'type':'output_text','text':json.dumps(r)}]}
  c.retain(message,100);self.assertEqual(c.report['case_id'],'planning-compact')
  child=copy.deepcopy(message);child['id']='child';child['agent']['agent_name']='/root/child';child['content'][0]['text']='child final';c.retain(child,200)
  empty=copy.deepcopy(message);empty['id']='empty';empty['content']=[];c.retain(empty,300);self.assertEqual(c.report_ms,100)
 def test_native_injection_and_single_continuation(self):
  for enabled in [False,True]:
   c=ws_runner.Collector(enabled,lambda call:'{"documents":[]}');sent=[]
   c.handle({'type':'response.created','response':{'id':'r'}},0,sent.append)
   event={'type':'response.output_item.done','item':{'id':'f','type':'function_call','call_id':'call1','name':'read_evidence','arguments':'{}'}}
   c.handle(event,1,sent.append);c.handle(event,1,sent.append)
   self.assertEqual(len(c.tool_calls),1)
   done=c.handle({'type':'response.completed','response':{'id':'r','output':[]}},2,sent.append)
   if enabled:self.assertFalse(done);self.assertTrue(c.handle({'type':'response.inject.created','response_id':'r'},3,sent.append));self.assertEqual(len(sent),1)
   else:self.assertTrue(done);self.assertEqual(sent,[]);self.assertEqual(c.next_input[0]['call_id'],'call1')
if __name__=='__main__':unittest.main()
