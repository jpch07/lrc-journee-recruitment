from decimal import Decimal

from app.assessment_runtime import active_assessment_definition
from app.management_corrections import raw_equivalent
from test_management_correction_api import setup, operation


def apply(client, url, headers, request):
    preview = client.post(url + '/preview', headers=headers, json=request)
    assert preview.status_code == 200, preview.text
    request['inputFingerprint'] = preview.json()['inputFingerprint']
    saved = client.put(url, headers=headers, json=request)
    assert saved.status_code == 200, saved.text
    return preview.json()


def test_canonical_raw_thresholds_and_nonrepresentable_grades():
    activity = next(a for a in active_assessment_definition().activities if a.scoring == 'target_average')
    c = activity.criteria[0].model_copy(deep=True)
    c.target, c.inputType, c.direction = Decimal(10), 'integer', 'higher'
    assert Decimal(raw_equivalent(activity, c, 1)) == 10
    assert Decimal(raw_equivalent(activity, c, '.8')) == 8
    assert raw_equivalent(activity, c, '.85') is None
    c.inputType, c.direction = 'duration', 'lower'
    assert Decimal(raw_equivalent(activity, c, '.5')) == 20
    assert raw_equivalent(activity, c, 0) is None


def test_full_activity_evaluation_is_atomic_editable_and_attributed(client):
    url, headers = setup(client)
    activity = next(a for a in active_assessment_definition().activities if a.scoring != 'target_average')
    request = operation(client, url)
    request.update(key=activity.key, value=None, criterionValues={c.key: '4' for c in activity.criteria})
    result = apply(client, url, headers, request)
    assert result['after']['activities'][activity.key]['score'] == 4
    state = client.get(url).json()
    assert state['activities'][activity.key]['author']
    request.update(revision=state['revision'], criterionValues={c.key: '3' for c in activity.criteria})
    result = apply(client, url, headers, request)
    assert result['after']['activities'][activity.key]['score'] == 3
    assert len(client.get(url).json()['activities'][activity.key]['history']) == 2
    request.update(revision=2)
    request['criterionValues'].pop(activity.criteria[0].key)
    assert client.post(url + '/preview', headers=headers, json=request).status_code == 422
    assert client.get(url).json()['revision'] == 2


def test_converted_results_and_uniform_grade_preserve_raw_inputs(client):
    url, headers = setup(client)
    activity = next(a for a in active_assessment_definition().activities if a.scoring == 'target_average')
    request = operation(client, url)
    request.update(key=activity.key, value=5)
    preview = apply(client, url, headers, request)
    for c in activity.criteria:
        assert Decimal(preview['activityRawValues'][activity.key][c.key]) == Decimal(c.target)
    state = client.get(url).json()
    request.update(revision=state['revision'], value=None, rawValues={c.key: str(c.target) for c in activity.criteria})
    high = next(c for c in activity.criteria if c.direction == 'higher')
    request['rawValues'][high.key] = str(Decimal(high.target) * 2)
    apply(client, url, headers, request)
    saved = client.get(url).json()['activities'][activity.key]
    assert Decimal(next(c for c in saved['criteria'] if c['key'] == high.key)['rawValue']) == Decimal(high.target) * 2
    request.update(revision=2, value=4)
    assert client.post(url + '/preview', headers=headers, json=request).status_code == 422


def test_nonfinite_criterion_grades_rejected(client):
    url, headers = setup(client)
    activity = next(a for a in active_assessment_definition().activities if a.scoring != 'target_average')
    request = operation(client, url)
    request.update(key=activity.key, value=None, criterionValues={c.key: 'NaN' for c in activity.criteria})
    assert client.post(url + '/preview', headers=headers, json=request).status_code == 422
