from __future__ import annotations

import hashlib
import heapq
import math
import random
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class RecruitCandidate:
    id: str
    name: str
    room_number: int | None = None


@dataclass(frozen=True)
class EvaluatorCandidate:
    id: str
    name: str
    role: str
    room_number: int | None = None


@dataclass(frozen=True)
class Pairing:
    evaluator_id: str
    recruit_id: str
    room_number: int | None
    slot: int
    repeated_pair: bool
    repeat_reason: str | None = None


@dataclass
class PairingResult:
    assignments: list[Pairing]
    warnings: list[str]


@dataclass
class RoomPlanResult:
    recruit_rooms: dict[str, int]
    evaluator_rooms: dict[str, int]
    mandatory_evaluators: set[str]
    warnings: list[str]


class _Edge:
    __slots__ = ("to", "rev", "cap", "cost", "initial_cap")

    def __init__(self, to: int, rev: int, cap: int, cost: int):
        self.to = to
        self.rev = rev
        self.cap = cap
        self.cost = cost
        self.initial_cap = cap


class MinCostFlow:
    def __init__(self, size: int):
        self.graph: list[list[_Edge]] = [[] for _ in range(size)]

    def add_edge(self, source: int, target: int, capacity: int, cost: int) -> _Edge:
        forward = _Edge(target, len(self.graph[target]), capacity, cost)
        reverse = _Edge(source, len(self.graph[source]), 0, -cost)
        self.graph[source].append(forward)
        self.graph[target].append(reverse)
        return forward

    def solve(self, source: int, target: int, requested_flow: int) -> tuple[int, int]:
        size = len(self.graph)
        potential = [0] * size
        flow = 0
        cost = 0
        infinity = 10**30
        while flow < requested_flow:
            distance = [infinity] * size
            previous_node = [-1] * size
            previous_edge = [-1] * size
            distance[source] = 0
            queue: list[tuple[int, int]] = [(0, source)]
            while queue:
                current_distance, node = heapq.heappop(queue)
                if current_distance != distance[node]:
                    continue
                for edge_index, edge in enumerate(self.graph[node]):
                    if edge.cap <= 0:
                        continue
                    candidate = current_distance + edge.cost + potential[node] - potential[edge.to]
                    if candidate < distance[edge.to]:
                        distance[edge.to] = candidate
                        previous_node[edge.to] = node
                        previous_edge[edge.to] = edge_index
                        heapq.heappush(queue, (candidate, edge.to))
            if distance[target] == infinity:
                break
            for node in range(size):
                if distance[node] < infinity:
                    potential[node] += distance[node]
            add = requested_flow - flow
            node = target
            while node != source:
                edge = self.graph[previous_node[node]][previous_edge[node]]
                add = min(add, edge.cap)
                node = previous_node[node]
            node = target
            while node != source:
                edge = self.graph[previous_node[node]][previous_edge[node]]
                edge.cap -= add
                self.graph[node][edge.rev].cap += add
                node = previous_node[node]
            flow += add
            cost += add * potential[target]
        return flow, cost


def _stable_tie(seed: str, *parts: str) -> int:
    digest = hashlib.sha256("|".join((seed, *parts)).encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % 997


def _primary_matching(
    recruits: list[RecruitCandidate],
    evaluators: list[EvaluatorCandidate],
    past_pairs: set[tuple[str, str]],
    seed: str,
    preferred_roles: list[str],
) -> tuple[list[Pairing], list[str], set[str]]:
    warnings: list[str] = []
    if not recruits:
        return [], warnings, set()
    if not evaluators:
        return [], ["No present evaluators are available for this assignment scope."], set()

    shortage = len(evaluators) < len(recruits)
    capacity = math.ceil(len(recruits) / len(evaluators)) if shortage else 1
    slots: list[tuple[EvaluatorCandidate, int]] = [
        (evaluator, load_number)
        for evaluator in evaluators
        for load_number in range(1, capacity + 1)
    ]

    source = 0
    slot_start = 1
    recruit_start = slot_start + len(slots)
    target = recruit_start + len(recruits)
    flow_graph = MinCostFlow(target + 1)
    edge_lookup: list[tuple[_Edge, EvaluatorCandidate, RecruitCandidate]] = []

    for slot_index, (evaluator, load_number) in enumerate(slots):
        node = slot_start + slot_index
        # Increasing marginal cost keeps shortage loads within one whenever feasible.
        load_cost = (load_number - 1) ** 2 * 20_000
        flow_graph.add_edge(source, node, 1, load_cost)
        for recruit_index, recruit in enumerate(recruits):
            repeated = (evaluator.id, recruit.id) in past_pairs
            repeat_cost = 1_000_000 if repeated else 0
            role_order = {role.casefold(): index for index, role in enumerate(preferred_roles)}
            role_cost = role_order.get(evaluator.role.casefold(), len(role_order)) * 5_000
            tie_cost = _stable_tie(seed, "primary", evaluator.id, recruit.id)
            edge = flow_graph.add_edge(node, recruit_start + recruit_index, 1, repeat_cost + role_cost + tie_cost)
            edge_lookup.append((edge, evaluator, recruit))
    for recruit_index in range(len(recruits)):
        flow_graph.add_edge(recruit_start + recruit_index, target, 1, 0)

    flow, _cost = flow_graph.solve(source, target, len(recruits))
    if flow < len(recruits):
        warnings.append(f"Only {flow} of {len(recruits)} recruits received a primary evaluator.")
    if shortage:
        warnings.append(
            f"Evaluator shortage: {len(evaluators)} evaluators cover {len(recruits)} recruits; balanced multi-recruit loads were used."
        )

    pairings: list[Pairing] = []
    used_evaluators: set[str] = set()
    seen_recruits: set[str] = set()
    for edge, evaluator, recruit in edge_lookup:
        if edge.initial_cap == 1 and edge.cap == 0 and recruit.id not in seen_recruits:
            repeated = (evaluator.id, recruit.id) in past_pairs
            pairings.append(
                Pairing(
                    evaluator_id=evaluator.id,
                    recruit_id=recruit.id,
                    room_number=recruit.room_number,
                    slot=1,
                    repeated_pair=repeated,
                    repeat_reason="No complete zero-repeat matching satisfied the current constraints." if repeated else None,
                )
            )
            used_evaluators.add(evaluator.id)
            seen_recruits.add(recruit.id)
    return pairings, warnings, used_evaluators


def _secondary_matching(
    recruits: list[RecruitCandidate],
    evaluators: list[EvaluatorCandidate],
    primary: list[Pairing],
    past_pairs: set[tuple[str, str]],
    prior_secondary_counts: dict[str, int],
    seed: str,
    preferred_roles: list[str],
) -> list[Pairing]:
    used_evaluators = {pair.evaluator_id for pair in primary}
    primary_pairs = {(pair.evaluator_id, pair.recruit_id) for pair in primary}
    available = [evaluator for evaluator in evaluators if evaluator.id not in used_evaluators]
    if not available or len(primary) < len(recruits):
        return []

    target_count = min(len(available), len(recruits))
    source = 0
    evaluator_start = 1
    recruit_start = evaluator_start + len(available)
    target = recruit_start + len(recruits)
    flow_graph = MinCostFlow(target + 1)
    edge_lookup: list[tuple[_Edge, EvaluatorCandidate, RecruitCandidate]] = []
    for evaluator_index, evaluator in enumerate(available):
        evaluator_node = evaluator_start + evaluator_index
        flow_graph.add_edge(source, evaluator_node, 1, 0)
        for recruit_index, recruit in enumerate(recruits):
            if (evaluator.id, recruit.id) in primary_pairs:
                continue
            repeated = (evaluator.id, recruit.id) in past_pairs
            repeat_cost = 1_000_000 if repeated else 0
            # After coverage and repeat avoidance, use the configured secondary-category order.
            role_order = {role.casefold(): index for index, role in enumerate(preferred_roles)}
            role_cost = role_order.get(evaluator.role.casefold(), len(role_order)) * 100_000
            fairness_cost = prior_secondary_counts.get(recruit.id, 0) * 10_000
            tie_cost = _stable_tie(seed, "secondary", evaluator.id, recruit.id)
            edge = flow_graph.add_edge(evaluator_node, recruit_start + recruit_index, 1, repeat_cost + role_cost + fairness_cost + tie_cost)
            edge_lookup.append((edge, evaluator, recruit))
    for recruit_index in range(len(recruits)):
        flow_graph.add_edge(recruit_start + recruit_index, target, 1, 0)
    flow_graph.solve(source, target, target_count)

    pairings: list[Pairing] = []
    seen_recruits: set[str] = set()
    for edge, evaluator, recruit in edge_lookup:
        if edge.initial_cap == 1 and edge.cap == 0 and recruit.id not in seen_recruits:
            repeated = (evaluator.id, recruit.id) in past_pairs
            pairings.append(
                Pairing(
                    evaluator_id=evaluator.id,
                    recruit_id=recruit.id,
                    room_number=recruit.room_number,
                    slot=2,
                    repeated_pair=repeated,
                    repeat_reason="No zero-repeat secondary matching satisfied the current constraints." if repeated else None,
                )
            )
            seen_recruits.add(recruit.id)
    return pairings


def generate_pairings(
    recruits: Iterable[RecruitCandidate],
    evaluators: Iterable[EvaluatorCandidate],
    *,
    room_based: bool,
    past_pairs: set[tuple[str, str]] | None = None,
    prior_secondary_counts: dict[str, int] | None = None,
    seed: str,
    primary_role_order: list[str] | None = None,
    secondary_role_order: list[str] | None = None,
    maximum_assessors: int = 2,
) -> PairingResult:
    recruits_list = list(recruits)
    evaluators_list = list(evaluators)
    past_pairs = past_pairs or set()
    prior_secondary_counts = prior_secondary_counts or {}
    warnings: list[str] = []
    assignments: list[Pairing] = []
    primary_role_order = primary_role_order or ["overall", "dossard"]
    secondary_role_order = secondary_role_order or ["dossard", "overall"]

    if room_based:
        rooms = sorted({item.room_number for item in recruits_list if item.room_number is not None})
        for room_number in rooms:
            room_recruits = [item for item in recruits_list if item.room_number == room_number]
            room_evaluators = [item for item in evaluators_list if item.room_number == room_number]
            primary, scope_warnings, _used = _primary_matching(
                room_recruits, room_evaluators, past_pairs, f"{seed}:room:{room_number}", primary_role_order
            )
            assignments.extend(primary)
            warnings.extend(f"Room {room_number}: {warning}" for warning in scope_warnings)
            if maximum_assessors >= 2 and len(room_evaluators) >= len(room_recruits):
                assignments.extend(
                    _secondary_matching(
                        room_recruits,
                        room_evaluators,
                        primary,
                        past_pairs,
                        prior_secondary_counts,
                        f"{seed}:room:{room_number}",
                        secondary_role_order,
                    )
                )
    else:
        primary, scope_warnings, _used = _primary_matching(
            recruits_list, evaluators_list, past_pairs, seed, primary_role_order
        )
        assignments.extend(primary)
        warnings.extend(scope_warnings)
        if maximum_assessors >= 2 and len(evaluators_list) >= len(recruits_list):
            assignments.extend(
                _secondary_matching(
                    recruits_list, evaluators_list, primary, past_pairs, prior_secondary_counts, seed,
                    secondary_role_order,
                )
            )

    repeat_count = sum(1 for assignment in assignments if assignment.repeated_pair)
    if repeat_count:
        warnings.append(f"{repeat_count} repeated evaluator–recruit pair(s) were forced by current constraints.")
    return PairingResult(assignments=assignments, warnings=warnings)


def generate_room_plan(
    recruits: Iterable[RecruitCandidate],
    evaluators: Iterable[EvaluatorCandidate],
    mandatory_rooms: dict[str, int],
    room_count: int,
    seed: str,
    primary_role_order: list[str] | None = None,
) -> RoomPlanResult:
    recruits_list = list(recruits)
    evaluators_list = list(evaluators)
    if room_count < 1:
        raise ValueError("At least one room is required.")
    if recruits_list and room_count > len(recruits_list):
        raise ValueError("The room count cannot exceed the number of present recruits.")

    randomizer = random.Random(int.from_bytes(hashlib.sha256(seed.encode()).digest()[:8], "big"))
    shuffled_recruits = recruits_list[:]
    randomizer.shuffle(shuffled_recruits)
    recruit_rooms: dict[str, int] = {}
    for index, recruit in enumerate(shuffled_recruits):
        recruit_rooms[recruit.id] = index % room_count + 1

    evaluator_by_id = {item.id: item for item in evaluators_list}
    evaluator_rooms: dict[str, int] = {}
    mandatory_present: set[str] = set()
    warnings: list[str] = []
    for evaluator_id, room_number in mandatory_rooms.items():
        if room_number < 1 or room_number > room_count:
            warnings.append(f"Mandatory evaluator {evaluator_id} refers to invalid room {room_number}.")
            continue
        if evaluator_id not in evaluator_by_id:
            warnings.append(f"A mandatory evaluator for room {room_number} is absent and was not placed.")
            continue
        evaluator_rooms[evaluator_id] = room_number
        mandatory_present.add(evaluator_id)

    remaining = [item for item in evaluators_list if item.id not in evaluator_rooms]
    randomizer.shuffle(remaining)
    # Place the configured primary categories first so they are spread across rooms.
    primary_role_order = primary_role_order or ["overall", "dossard"]
    role_order = {role.casefold(): index for index, role in enumerate(primary_role_order)}
    remaining.sort(key=lambda item: role_order.get(item.role.casefold(), len(role_order)))

    recruit_count_by_room = {
        room: sum(1 for value in recruit_rooms.values() if value == room)
        for room in range(1, room_count + 1)
    }

    def placement_score(evaluator: EvaluatorCandidate, room: int) -> tuple[float, float, int]:
        assigned = [evaluator_by_id[item_id] for item_id, value in evaluator_rooms.items() if value == room]
        evaluator_count = len(assigned)
        primary_count = sum(
            1 for item in assigned
            if item.role.casefold() == primary_role_order[0].casefold()
        )
        recruit_count = recruit_count_by_room[room]
        primary_deficit = max(recruit_count - evaluator_count, 0)
        # Larger deficits are preferred; first-priority assessors are spread proportionally.
        primary_ratio = primary_count / max(recruit_count, 1)
        balance_ratio = evaluator_count / max(recruit_count, 1)
        is_first_priority = evaluator.role.casefold() == primary_role_order[0].casefold()
        return (-primary_deficit, primary_ratio if is_first_priority else balance_ratio, room)

    for evaluator in remaining:
        selected_room = min(range(1, room_count + 1), key=lambda room: placement_score(evaluator, room))
        evaluator_rooms[evaluator.id] = selected_room

    for room in range(1, room_count + 1):
        recruit_count = recruit_count_by_room[room]
        evaluator_count = sum(1 for value in evaluator_rooms.values() if value == room)
        if recruit_count and evaluator_count == 0:
            warnings.append(f"Room {room} has recruits but no evaluators.")
        elif evaluator_count < recruit_count:
            warnings.append(
                f"Room {room} has {recruit_count} recruits and {evaluator_count} evaluators; shortage multi-load rules will apply."
            )
        if evaluator_count > recruit_count * 2 and recruit_count:
            warnings.append(f"Room {room} has evaluators who will remain on standby because recruits accept at most two evaluators.")

    return RoomPlanResult(
        recruit_rooms=recruit_rooms,
        evaluator_rooms=evaluator_rooms,
        mandatory_evaluators=mandatory_present,
        warnings=warnings,
    )
