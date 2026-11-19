"""Regression checks for conditional remediation and truthful outcomes."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from remediation.apply import k8s
from remediation.branches import stubs
from remediation.plan_schema import ExactTarget, PlanChange, SLOGate, TypedPlanSpec
from remediation.slo import SLOSnapshot, evaluate_gates
from remediation import worker


@pytest.fixture(autouse=True)
def execution_context(monkeypatch):
    monkeypatch.setenv('CONTEXT', 'test-cluster')


def spec(action='config_patch'):
    return TypedPlanSpec(action, 'Config', ExactTarget('test-cluster', 'otel-demo', 'ConfigMap' if action == 'config_patch' else 'Deployment', k8s.FLAGD_CM if action == 'config_patch' else 'payment'), 'fp', PlanChange('test', {'flag_key':'f','defaultVariant':'off','resource_version':'10'} if action == 'config_patch' else {'replicas':3,'resource_version':'10'}, {'flag_key':'f','defaultVariant':'on'} if action == 'config_patch' else {'replicas':2}), 'low', 1, SLOGate(require_fresh=False), 'i', 'payment', .9)


def live_config(rv='10', value='on'):
    return {'resource_version':rv, 'doc':{'flags':{'f':{'defaultVariant':value},'other':{'defaultVariant':'keep'}}}}


def test_config_patch_has_atomic_precondition(monkeypatch):
    monkeypatch.setattr(k8s, 'read_flagd_config', lambda ns: live_config())
    call = Mock(return_value=(0, '{}', ''))
    monkeypatch.setattr(k8s, '_kubectl', call)
    assert k8s.apply_config_patch({'plan_spec':spec().to_dict()})['status'] == 'succeeded'
    args = call.call_args.args[0]
    assert json.loads(args[args.index('-p')+1])['metadata']['resourceVersion'] == '10'


@pytest.mark.parametrize('rv,value,inverse', [('11','on',False),('11','newer',True)])
def test_config_refuses_stale_forward_or_changed_inverse(monkeypatch, rv, value, inverse):
    monkeypatch.setattr(k8s, 'read_flagd_config', lambda ns: live_config(rv,value))
    call=Mock(); monkeypatch.setattr(k8s, '_kubectl',call)
    out=k8s.apply_exact_flagd_patch({'plan_spec':spec().to_dict()},use_inverse=inverse)
    assert out.get('status') == 'failed'
    call.assert_not_called()


def test_config_inverse_preserves_other_flags_with_current_version(monkeypatch):
    monkeypatch.setattr(k8s, 'read_flagd_config', lambda ns: live_config('11','off'))
    call=Mock(return_value=(0,'{}','')); monkeypatch.setattr(k8s, '_kubectl',call)
    assert k8s.rollback_config_patch({'plan_spec':spec().to_dict()})['status']=='succeeded'
    args=call.call_args.args[0]; body=json.loads(args[args.index('-p')+1])
    assert body['metadata']['resourceVersion']=='11'
    assert json.loads(body['data'][k8s.FLAGD_KEY])['flags']['other']['defaultVariant']=='keep'


def test_wrong_cluster_cannot_mutate(monkeypatch):
    monkeypatch.setenv('CONTEXT','other-cluster')
    read=Mock(); call=Mock(); monkeypatch.setattr(k8s,'read_flagd_config',read);monkeypatch.setattr(k8s,'_kubectl',call)
    assert not k8s.apply_config_patch({'plan_spec':spec().to_dict()})['attempted']
    read.assert_not_called();call.assert_not_called()


@pytest.mark.parametrize('current', [stubs.MAX_REPLICAS,stubs.MAX_REPLICAS+3,None,-1])
def test_scale_proposal_rejects_cap_or_invalid_counts(monkeypatch,current):
    monkeypatch.setattr(k8s,'read_workload_revision',lambda *a: {'kind':'Deployment','replicas':current,'resource_version':'10'})
    c=SimpleNamespace(node_id='payment',confidence=.9,to_dict=lambda:{})
    assert not stubs.propose_resource_scale(c)['executable']


def test_scale_zero_proposes_one_without_resources(monkeypatch):
    monkeypatch.setattr(k8s,'read_workload_revision',lambda *a: {'kind':'Deployment','replicas':0,'resource_version':'10','resources':{'requests':{'cpu':'1'}}})
    c=SimpleNamespace(node_id='payment',confidence=.9,to_dict=lambda:{})
    plan=stubs.propose_resource_scale(c)['plan_spec']
    assert plan['change']['patch']=={'replicas':1,'resource_version':'10'}
    assert plan['change']['inverse']=={'replicas':0}


def test_scale_uses_single_conditional_patch(monkeypatch):
    monkeypatch.setattr(k8s,'read_workload_revision',lambda *a: {'kind':'Deployment','replicas':2,'resource_version':'10'})
    call=Mock(return_value=(0,'{"metadata":{"resourceVersion":"11"}}',''));monkeypatch.setattr(k8s,'_kubectl',call)
    out=k8s.apply_resource_patch({'plan_spec':spec('scale_up').to_dict()})
    assert out['status']=='succeeded'
    args=call.call_args.args[0];body=json.loads(args[args.index('-p')+1])
    assert body=={'metadata':{'resourceVersion':'10'},'spec':{'replicas':3}}
    assert out['resource_version_after']=='11'


def test_scale_inverse_refuses_newer_operator_change(monkeypatch):
    monkeypatch.setattr(k8s,'read_workload_revision',lambda *a: {'kind':'Deployment','replicas':4,'resource_version':'12'})
    call=Mock();monkeypatch.setattr(k8s,'_kubectl',call)
    out=k8s.apply_resource_patch({'plan_spec':spec('scale_up').to_dict(),'apply_result':{'resource_version_after':'11'}},use_inverse=True)
    assert out['status']=='failed';call.assert_not_called()


@pytest.mark.parametrize('values', [(None,None,None),(0,None,1),(float('nan'),1,1),(0,float('inf'),1)])
def test_missing_partial_nonfinite_metrics_never_pass(values):
    snap=SLOSnapshot('payment',*values,True,'now')
    result=evaluate_gates(snap,error_rate_max=.05,latency_p99_ms_max=800,success_rate_min=.95,require_fresh=False)
    assert result['pass'] is False
    assert result['verified'] is False


def test_observe_honors_window_and_requires_fresh(monkeypatch):
    snap=SLOSnapshot('payment',0,20,1,False,'now')
    call=Mock(return_value=snap);monkeypatch.setattr('remediation.slo.snapshot_service',call);monkeypatch.setattr(worker.time,'sleep',lambda _:None)
    result=worker._observe(spec(),{})
    assert call.call_args.kwargs['window']=='60s'
    assert result['pass'] is False


def test_unknown_postflight_never_triggers_inverse(monkeypatch):
    monkeypatch.setattr('remediation.policy.check_plan_executable',lambda s: SimpleNamespace(allowed=True,to_dict=lambda:{}))
    monkeypatch.setattr('remediation.slo.snapshot_service',lambda *a,**kw:SLOSnapshot('payment',0,20,1,True,'now'))
    monkeypatch.setattr(worker,'_dispatch_execute',lambda *a:{'attempted':True,'status':'succeeded'})
    monkeypatch.setattr(worker,'_observe',lambda *a:{'pass':False,'verified':False,'reason':'metrics missing'})
    inverse=Mock();monkeypatch.setattr(worker,'_dispatch_rollback',inverse)
    store=Mock();out=worker.execute_plan({'id':'p','plan_spec':spec().to_dict()},store)
    assert out['status']=='failed'; assert 'unverified' in out['error'];inverse.assert_not_called()


def test_rollback_context_mismatch_cannot_fall_back_to_row(monkeypatch):
    monkeypatch.setenv('CONTEXT','other-cluster')
    call=Mock();monkeypatch.setattr(k8s,'_kubectl',call)
    s=spec('rollback_to_last_good')
    out=k8s.apply_dependency_rollback({'plan_spec':s.to_dict(),'candidate_node':'payment'})
    assert not out['attempted'];call.assert_not_called()


@pytest.mark.parametrize(
    'revision, command',
    [
        ('7', ['arollouts', 'undo', 'payment', '-n', 'otel-demo', '--to-revision', '7']),
        (None, ['arollouts', 'abort', 'payment', '-n', 'otel-demo']),
    ],
)
def test_argo_rollback_failure_does_not_run_a_different_action(monkeypatch, revision, command):
    s = spec('rollback_to_last_good')
    s.target = ExactTarget('test-cluster', 'otel-demo', 'Rollout', 'payment', revision=revision)
    s.change = PlanChange('test', {'revision': revision}, {'revision': 'current'})
    call = Mock(return_value=(1, '', 'argo command failed'))
    monkeypatch.setattr(k8s, '_kubectl', call)

    out = k8s.apply_dependency_rollback({'plan_spec': s.to_dict()})

    assert out['status'] == 'failed'
    assert out['revision'] == revision
    call.assert_called_once_with(command)


def test_unknown_preflight_stops_fresh_required_plan(monkeypatch):
    monkeypatch.setattr('remediation.policy.check_plan_executable',lambda s: SimpleNamespace(allowed=True,to_dict=lambda:{}))
    monkeypatch.setattr('remediation.slo.snapshot_service',lambda *a,**kw:SLOSnapshot('payment',float('nan'),20,1,True,'now'))
    dispatch=Mock();monkeypatch.setattr(worker,'_dispatch_execute',dispatch)
    s=spec();s.slo_gates=SLOGate(require_fresh=True)
    out=worker.execute_plan({'id':'p','plan_spec':s.to_dict()},Mock())
    assert not out['attempted'];dispatch.assert_not_called()


def test_observe_waits_for_entire_post_action_window(monkeypatch):
    monkeypatch.setenv('REMEDIATION_OBSERVE_SLEEP_S','15')
    sleep=Mock();monkeypatch.setattr(worker.time,'sleep',sleep)
    monkeypatch.setattr('remediation.slo.snapshot_service',lambda *a,**kw:SLOSnapshot('payment',0,20,1,True,'now'))
    assert worker._observe(spec(),{})['pass']
    assert sleep.call_args.args[0] >= 60


def test_nonfinite_snapshot_is_safe_for_jsonb():
    snap=SLOSnapshot('payment',float('nan'),float('inf'),1,True,'now')
    body=snap.to_dict()
    assert body['error_rate'] is None
    assert body['latency_p99_ms'] is None
    json.dumps(body,allow_nan=False)


@pytest.mark.parametrize('age, fresh', [(10,True),(31,False),(float('nan'),False),(None,False)])
def test_snapshot_checks_underlying_source_age(monkeypatch, age, fresh):
    from remediation.slo import snapshot_service
    def instant(query):
        if 'timestamp(' in query:
            return [] if age is None else [{'value':[0,str(age)]}]
        value = 20 if 'histogram_quantile' in query else (1 if 'status_code!=' in query else 0)
        return [{'value':[0,str(value)]}]
    monkeypatch.setattr('remediation.slo._instant',instant)
    assert snapshot_service('payment',window='60s').fresh is fresh


def test_snapshot_requires_oldest_contributing_series_to_be_fresh(monkeypatch):
    from remediation.slo import snapshot_service
    queries=[]
    def instant(query):
        queries.append(query)
        if 'timestamp(' in query:
            # A fresh series must not conceal a 45-second-old contributor.
            value = 45 if 'min(timestamp(' in query and ' and rate(' in query else 5
        else:
            value = 20 if 'histogram_quantile' in query else (1 if 'status_code!=' in query else 0)
        return [{'value':[0,str(value)]}]
    monkeypatch.setattr('remediation.slo._instant',instant)
    assert snapshot_service('payment',window='60s').fresh is False
    assert all('[60s]' in q for q in queries if 'timestamp(' in q)
