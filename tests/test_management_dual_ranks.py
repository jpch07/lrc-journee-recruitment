from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from app.assessment_runtime import active_assessment_definition, activate_assessment_definition, reset_assessment_definition
from app.db import SessionLocal
from app.models import AssessmentSystem, GeneralAssessment, Recruit
from app.routes_viewer import _aggregate_results, journey_view
from app.services import create_journey, result_snapshot
from app.tenant import select_system, reset_system
from test_viewer_performance_c1 import _login, _populate_test_journeys


def test_management_table_and_both_profile_scopes_agree(client):
    _login(client)
    with SessionLocal() as db:
        j1, j2, draft, r1, r2, r3, rd = _populate_test_journeys(db)
        identifiers = [(j1.id, r1.id), (j1.id, r2.id), (j2.id, r3.id)]
        before = result_snapshot(db, j1)
    combined = client.get('/api/view/completed').json()['results']['rows']
    assert len(combined) == 3
    for jid, rid in identifiers:
        expected = next(row for row in combined if row['recruitId'] == rid)
        table = client.get(f'/api/view/journeys/{jid}').json()['results']['rows']
        row = next(row for row in table if row['recruitId'] == rid)
        for suffix in ['', '?scope=completed']:
            response = client.get(f'/api/view/journeys/{jid}/recruits/{rid}/profile{suffix}')
            assert response.status_code == 200, response.text
            profile = response.json()['result']
            for field in ['overallRank', 'overallPopulation', 'journeyRank', 'journeyPopulation', 'overallScore']:
                assert profile[field] == row[field] == expected[field]
        assert row['overallPopulation'] == 3
    local = {row['recruitId']: row for row in before['rows']}
    for row in combined:
        if row['recruitId'] in local:
            assert row['journeyRank'] == local[row['recruitId']]['overallRank']
            assert row['journeyPopulation'] == 2


@pytest.mark.parametrize('status', ['draft', 'active'])
def test_unfinished_journee_has_local_rank_only(client, status):
    _login(client)
    with SessionLocal() as db:
        _, _, journey, *_, recruit = _populate_test_journeys(db)
        journey.status = status
        db.commit()
    table = client.get(f'/api/view/journeys/{journey.id}').json()['results']['rows'][0]
    profile = client.get(f'/api/view/journeys/{journey.id}/recruits/{recruit.id}/profile').json()['result']
    for row in [table, profile]:
        assert row['overallRank'] is None
        assert row['overallPopulation'] == 3
        assert row['journeyRank'] == 1
        assert row['journeyPopulation'] == 1


@pytest.mark.parametrize('mode, expected', [('competition', [1, 1, 3]), ('dense', [1, 1, 2]), ('ordinal', [1, 2, 3])])
def test_both_ranks_preserve_configured_ties_and_source(mode, expected):
    definition = active_assessment_definition().model_copy(deep=True)
    definition.scoring.ranking = mode
    token = activate_assessment_definition(definition)
    try:
        with SessionLocal() as db:
            journey = create_journey(db, 'Rank fixture', date(2026, 8, 1), 1, 'Test')
            recruits = [Recruit(journey_id=journey.id, name=f'Person {i}', present=True) for i in range(3)]
            db.add_all(recruits)
            db.flush()
            for recruit, value in zip(recruits, [1, 1, 0]):
                db.add(GeneralAssessment(recruit_id=recruit.id, punctuality=Decimal(value), respect=Decimal(value), seriousness=Decimal(value)))
            db.commit()
            snapshot = result_snapshot(db, journey)
            original = deepcopy(snapshot)
            aggregate = _aggregate_results([(journey, snapshot)])
        assert snapshot == original
        assert sorted(row['overallRank'] for row in aggregate['rows']) == expected
        assert sorted(row['journeyRank'] for row in aggregate['rows']) == expected
    finally:
        reset_assessment_definition(token)


def test_single_journee_overall_rank_excludes_other_workspaces(client):
    _login(client)
    with SessionLocal() as db:
        first, *_ = _populate_test_journeys(db)
        source = db.get(AssessmentSystem, first.system_id)
        other = AssessmentSystem(name='Other', slug='other-rank-test', draft_json=source.draft_json, updated_by='Test')
        db.add(other)
        db.flush()
        token = select_system(other.id)
        try:
            journey = create_journey(db, 'Other completed', date(2026, 8, 2), 1, 'Test')
            journey.status = 'completed'
            db.add(Recruit(journey_id=journey.id, name='Other person', present=True))
            db.commit()
        finally:
            reset_system(token)
        # Explicit system filter also protects internal callers without request context.
        payload = journey_view(first.id, context=None, db=db)
        assert all(row['overallPopulation'] == 3 for row in payload['results']['rows'])


def test_management_assets_have_distinct_ranks_and_color_first():
    static = Path(__file__).parents[1] / 'app' / 'static'
    viewer = (static / 'viewer.js').read_text(encoding='utf-8')
    table = viewer.split('function overallTable(rows) {', 1)[1].split('function dimensionTable', 1)[0]
    assert '<thead><tr><th>Color</th>' in table
    assert '>Rank</th>' in table and 'Journee rank' not in table and 'Overall rank' not in table
    assert 'result.journeyRank' in viewer and 'result.overallPopulation' in viewer
    admin = (static / 'admin.js').read_text(encoding='utf-8').split('function overallResultsTable(rows) {', 1)[1]
    assert '<thead><tr><th>Color</th>' in admin.split('function ', 1)[0]


def test_management_ranks_use_quiet_stacked_numbers():
    static = Path(__file__).parents[1] / 'app' / 'static'
    viewer = (static / 'viewer.js').read_text(encoding='utf-8')
    table = viewer.split('function overallTable(rows) {', 1)[1].split('function dimensionTable', 1)[0]
    assert 'rankDisplay(row.overallRank, row.overallPopulation)' in table
    assert 'rankDisplay(row.journeyRank, row.journeyPopulation)' not in table
    assert 'class="rank-number"' not in table
    assert ' · completed Journees</small>' not in viewer
    assert 'class="result-rank-value"' in viewer
    assert 'class="result-rank-total"' in viewer
