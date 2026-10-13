import asyncio
import json

import pytest

from agent.forms import OperationPaused
from agent.models import Search
from agent.discovery import acknowledge_candidates, discover_candidates


async def discover(search, progress, loader, *, limit=1, max_pages=1, known=(), can_act=lambda: True):
    return await discover_candidates(
        search, limit=limit, max_pages=max_pages, known_ids=set(known),
        progress=progress, load_page=loader, can_act=can_act,
    )


def acknowledge(progress, ids):
    acknowledge_candidates(progress, ids)


@pytest.mark.asyncio
async def test_small_runs_rotate_all_title_country_city_combinations():
    search = Search(titles=['Engineer', 'Designer'], countries=['Canada', 'Germany'], cities=['Toronto', 'Berlin'])
    progress, visited = {}, []

    async def load(query):
        visited.append((query.title, query.country, query.city, query.page))
        return [str(len(visited))]

    for _ in range(8):
        ids = await discover(search, progress, load)
        acknowledge(progress, ids)
        progress = json.loads(json.dumps(progress))

    assert visited == [
        ('Engineer', 'Canada', 'Toronto', 0), ('Engineer', 'Canada', 'Berlin', 0),
        ('Engineer', 'Germany', 'Toronto', 0), ('Engineer', 'Germany', 'Berlin', 0),
        ('Designer', 'Canada', 'Toronto', 0), ('Designer', 'Canada', 'Berlin', 0),
        ('Designer', 'Germany', 'Toronto', 0), ('Designer', 'Germany', 'Berlin', 0),
    ]


@pytest.mark.asyncio
async def test_known_ids_do_not_use_budget_or_end_pagination():
    search, progress, visited = Search(titles=['Engineer']), {}, []

    async def load(query):
        visited.append(query.page)
        return {0: ['1', '2'], 1: ['2', '3'], 2: ['4', '5']}[query.page]

    ids = await discover(search, progress, load, known=['1', '2', '3'], limit=2, max_pages=3)
    assert ids == ['4', '5']
    assert visited == [0, 1, 2]


@pytest.mark.asyncio
async def test_page_cursor_survives_restart_instead_of_rechecking_first_page():
    search, progress, visited = Search(titles=['Engineer']), {}, []

    async def load(query):
        visited.append(query.page)
        return [str(query.page + 1)]

    ids = await discover(search, progress, load)
    acknowledge(progress, ids)
    restarted = json.loads(json.dumps(progress))
    assert await discover(search, restarted, load, known=ids) == ['2']
    assert visited == [0, 1]


@pytest.mark.asyncio
async def test_one_run_fills_budget_across_pages_without_discarding_pending_candidates():
    search, progress, visited = Search(), {}, []

    async def load(query):
        visited.append(query.page)
        return {0: ['101', '102'], 1: ['102', '103'], 2: ['103', '102']}[query.page]

    assert await discover(search, progress, load, limit=10, max_pages=10) == ['101', '102', '103']
    assert visited == [0, 1, 2]
    acknowledge(progress, ['101'])
    # A later failed detail read must retain candidates from both loaded pages.
    assert await discover(search, progress, load, limit=2) == ['102', '103']
    assert visited == [0, 1, 2]


@pytest.mark.asyncio
async def test_cancelled_later_page_retains_candidates_from_prior_pages():
    search, progress, visited = Search(), {}, []

    async def load(query):
        visited.append(query.page)
        if query.page == 1:
            raise asyncio.CancelledError()
        return ['101', '102']

    with pytest.raises(asyncio.CancelledError):
        await discover(search, progress, load, limit=3, max_pages=3)

    async def recover(query):
        visited.append(query.page)
        return ['103']

    assert await discover(search, progress, recover, limit=2, known=['101']) == ['102', '103']
    assert visited == [0, 1, 1]


@pytest.mark.asyncio
async def test_pending_page_remainder_does_not_starve_other_queries():
    search, progress, visited = Search(titles=['Engineer', 'Designer']), {}, []

    async def load(query):
        visited.append((query.title, query.page))
        return ['1', '2', '3'] if query.title == 'Engineer' else ['4', '5', '6']

    found = []
    for _ in range(4):
        ids = await discover(search, progress, load)
        found += ids
        acknowledge(progress, ids)
    assert found == ['1', '4', '2', '5']
    assert visited == [('Engineer', 0), ('Designer', 0)]


@pytest.mark.asyncio
async def test_unacknowledged_candidates_survive_failed_detail_read():
    search, progress = Search(titles=['Engineer']), {}

    async def load(query):
        return ['1', '2', '3']

    assert await discover(search, progress, load, limit=2) == ['1', '2']
    acknowledge(progress, ['1'])  # Detail/storage work failed before ID 2 completed.
    restarted = json.loads(json.dumps(progress))
    assert await discover(search, restarted, load, limit=2, known=['1']) == ['2', '3']


@pytest.mark.asyncio
async def test_known_pending_ids_are_pruned_without_wasting_new_budget():
    search, progress, visited = Search(), {}, []

    async def load(query):
        visited.append(query.page)
        return ['1', '2'] if query.page == 0 else ['3']

    assert await discover(search, progress, load) == ['1']
    assert await discover(search, progress, load, known=['1', '2']) == ['3']
    assert visited == [0, 1]


@pytest.mark.asyncio
async def test_duplicate_ids_across_queries_are_returned_once_per_run():
    search, progress = Search(titles=['Engineer', 'Designer']), {}

    async def load(query):
        return ['1', '1', '2'] if query.title == 'Engineer' else ['1', '3']

    assert await discover(search, progress, load, limit=3, max_pages=3) == ['1', '2', '3']


@pytest.mark.asyncio
async def test_empty_and_repeated_pages_reset_only_their_query_for_next_run():
    search, progress, visited = Search(titles=['Engineer', 'Designer']), {}, []

    async def load(query):
        visited.append((query.title, query.page))
        return ['1'] if query.title == 'Engineer' else []

    assert await discover(search, progress, load, known=['1'], limit=2, max_pages=10) == []
    assert visited == [('Engineer', 0), ('Designer', 0), ('Engineer', 1)]
    visited.clear()
    assert await discover(search, progress, load, known=['1'], max_pages=1) == []
    assert visited == [('Designer', 0)]


@pytest.mark.asyncio
async def test_reordered_repeated_page_is_detected_across_runs():
    search, progress, visited = Search(), {}, []

    async def load(query):
        visited.append(query.page)
        return ['1', '2'] if query.page == 0 else ['2', '1']

    await discover(search, progress, load, known=['1', '2'], max_pages=1)
    await discover(search, progress, load, known=['1', '2'], max_pages=5)
    await discover(search, progress, load, known=['1', '2'], max_pages=1)
    assert visited == [0, 1, 0]


@pytest.mark.asyncio
async def test_page_budget_applies_to_known_results_on_every_run():
    search, progress, visited = Search(), {}, []

    async def load(query):
        visited.append(query.page)
        return [str(query.page)]

    for _ in range(2):
        assert await discover(search, progress, load, known=map(str, range(100)), max_pages=3) == []
    assert visited == [0, 1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_definition_change_resets_cursors_and_pending_ids():
    search, progress, visited = Search(titles=['Engineer']), {}, []

    async def load(query):
        visited.append((query.title, query.page))
        return ['1', '2'] if query.title == 'Engineer' else ['3']

    await discover(search, progress, load)
    search.titles = ['Designer']
    assert await discover(search, progress, load) == ['3']
    assert visited == [('Engineer', 0), ('Designer', 0)]


@pytest.mark.asyncio
async def test_name_change_preserves_progress_but_filter_change_resets_it():
    search, progress, visited = Search(), {}, []

    async def load(query):
        visited.append(query.page)
        return [str(len(visited))]

    acknowledge(progress, await discover(search, progress, load))
    search.name = 'Renamed search'
    acknowledge(progress, await discover(search, progress, load))
    search.required_skills = ['Python']
    await discover(search, progress, load)
    assert visited == [0, 1, 0]


@pytest.mark.asyncio
async def test_query_parameters_preserve_linkedin_filters():
    search = Search(titles=['Engineer'], countries=['Canada'], cities=['Toronto'], workplaces=['remote', 'hybrid'], posted_within_days=3, easy_apply_only=True)
    queries = []

    async def load(query):
        queries.append(query.params)
        return ['1']

    await discover(search, {}, load)
    assert queries == [{'keywords': 'Engineer', 'location': 'Toronto, Canada', 'f_TPR': 'r259200', 'f_WT': '2,3', 'f_AL': 'true', 'start': '0'}]


@pytest.mark.asyncio
async def test_failed_or_cancelled_page_load_keeps_same_page_for_retry():
    search, progress, visited = Search(), {}, []

    async def fail(query):
        visited.append(query.page)
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await discover(search, progress, fail)

    async def recover(query):
        visited.append(query.page)
        return ['1']

    assert await discover(search, progress, recover) == ['1']
    assert visited == [0, 0]


@pytest.mark.asyncio
async def test_paused_operation_does_not_load_or_lose_pending_ids():
    search, progress = Search(), {}

    async def load(query):
        return ['1', '2']

    await discover(search, progress, load)
    with pytest.raises(OperationPaused):
        await discover(search, progress, load, can_act=lambda: False)
    assert await discover(search, progress, load, limit=2) == ['1', '2']


@pytest.mark.asyncio
@pytest.mark.parametrize(('limit', 'max_pages'), [(0, 1), (1, 0), (-1, 1), (1, -1)])
async def test_nonpositive_budgets_do_no_work(limit, max_pages):
    async def load(query):
        raise AssertionError('Navigation is forbidden with an empty budget')

    assert await discover(Search(), {}, load, limit=limit, max_pages=max_pages) == []
