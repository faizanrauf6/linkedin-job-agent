"""Bounded, resumable LinkedIn search scheduling without browser or storage I/O.

The caller owns persistence of ``progress`` (including when an operation raises).
Returned IDs remain pending until their detail result has been durably stored;
acknowledge them after that write, or supply them as known IDs on the next run.
This makes an interrupted detail read safe to retry without losing page leftovers.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from hashlib import sha256
from itertools import product
import json

from .forms import OperationPaused
from .models import Search


@dataclass(frozen=True)
class DiscoveryQuery:
    index: int
    title: str
    country: str
    city: str
    page: int
    params: dict[str, str]


def acknowledge_candidates(progress: dict, inspected_ids: Iterable[str]) -> None:
    """Remove only candidates whose inspection/storage work has completed."""
    inspected = {str(value) for value in inspected_ids}
    for query in progress.get('queries', []):
        query['pending'] = [value for value in query['pending'] if value not in inspected]


def _prepare(search: Search, progress: dict) -> list[tuple[str, str, str]]:
    combinations = list(product(search.titles or [''], search.countries or [''], search.cities or ['']))
    definition = search.model_dump(mode='json', exclude={'id', 'name', 'enabled'})
    fingerprint = sha256(json.dumps(definition, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if (progress.get('version') != 1 or progress.get('fingerprint') != fingerprint
            or len(progress.get('queries', [])) != len(combinations)):
        progress.clear()
        progress.update(version=1, fingerprint=fingerprint, next_query=0,
                        queries=[{'page': 0, 'pending': [], 'signatures': []} for _ in combinations])
    return combinations


def _query(search: Search, index: int, combination: tuple[str, str, str], page: int) -> DiscoveryQuery:
    title, country, city = combination
    params = {'keywords': title, 'location': ', '.join(value for value in (city, country) if value),
              'f_TPR': 'r' + str(search.posted_within_days * 86400), 'start': str(page * 25)}
    if search.workplaces:
        params['f_WT'] = ','.join({'onsite': '1', 'remote': '2', 'hybrid': '3'}[value] for value in search.workplaces)
    if search.easy_apply_only:
        params['f_AL'] = 'true'
    return DiscoveryQuery(index, title, country, city, page, params)


async def discover_candidates(
    search: Search,
    limit: int,
    max_pages: int,
    known_ids: Iterable[str],
    progress: dict,
    load_page: Callable[[DiscoveryQuery], Awaitable[Iterable[str]]],
    can_act: Callable[[], bool] = lambda: True,
) -> list[str]:
    """Return at most ``limit`` new IDs using at most ``max_pages`` page loads.

    ``load_page`` performs navigation, lazy scrolling and sign-in checks, and
    returns LinkedIn IDs in card order. Its exceptions propagate without moving
    that query's cursor. ``progress`` contains only JSON values, and is mutated
    after each completed page load. Persist it in a caller ``finally`` block.

    Each query receives one turn before the next query. A turn consumes pending
    candidates or loads one page, so even a one-job run rotates fairly. Pending
    page remainders are selected before advancing that query to its next page,
    and remain retained until the caller acknowledges durable inspection.
    Empty/repeated pages reset that query for the next run; already known pages
    continue pagination within the run's navigation budget.
    """
    if limit <= 0 or max_pages <= 0:
        return []
    combinations = _prepare(search, progress)
    known = {str(value) for value in known_ids}
    acknowledge_candidates(progress, known)
    selected: list[str] = []
    selected_ids: set[str] = set()
    exhausted: set[int] = set()
    pages_used = idle_turns = 0
    count = len(combinations)

    while len(selected) < limit and idle_turns < count:
        if not can_act():
            raise OperationPaused('Agent paused or configuration changed')
        index = progress['next_query']
        state = progress['queries'][index]
        made_progress = False
        has_unselected = any(value not in selected_ids for value in state['pending'])
        if index not in exhausted and not has_unselected and pages_used < max_pages:
            query = _query(search, index, combinations[index], state['page'])
            # Do not change the cursor until navigation and extraction succeed.
            page_ids = list(dict.fromkeys(str(value) for value in await load_page(query)))
            signature = sha256(json.dumps(sorted(page_ids), separators=(',', ':')).encode()).hexdigest()
            pages_used += 1
            made_progress = True
            if not page_ids or signature in state['signatures']:
                state['page'] = 0
                state['signatures'] = []
                exhausted.add(index)
            else:
                state['page'] += 1
                # Detect repeated pagination windows across runs, with bounded metadata.
                state['signatures'] = (state['signatures'] + [signature])[-100:]
            # Even a repeated window can contain uninspected IDs after a restart.
            state['pending'] = list(dict.fromkeys(
                state['pending'] + [value for value in page_ids if value not in known]
            ))

        for value in state['pending']:
            if value not in selected_ids:
                selected.append(value)
                selected_ids.add(value)
                made_progress = True
                if len(selected) >= limit:
                    break
        progress['next_query'] = (index + 1) % count
        idle_turns = 0 if made_progress else idle_turns + 1

    return selected
