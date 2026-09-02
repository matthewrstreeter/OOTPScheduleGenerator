import argparse
import math
import os
import random
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from xml.dom import minidom

import networkx as nx
from ortools.sat.python import cp_model

DAY_MAP = {
    "sunday": 1, "sun": 1, "1": 1,
    "monday": 2, "mon": 2, "2": 2,
    "tuesday": 3, "tue": 3, "3": 3,
    "wednesday": 4, "wed": 4, "4": 4,
    "thursday": 5, "thu": 5, "5": 5,
    "friday": 6, "fri": 6, "6": 6,
    "saturday": 7, "sat": 7, "7": 7,
}

# Avoid oversubscribing threads on small servers (e.g. 1 vCPU droplets).
CPU_SEARCH_WORKERS = max(1, min(8, os.cpu_count() or 1))

MONTH_MAP = {
    "january": 1, "jan": 1, "1": 1,
    "february": 2, "feb": 2, "2": 2,
    "march": 3, "mar": 3, "3": 3,
    "april": 4, "apr": 4, "4": 4,
    "may": 5, "5": 5,
    "june": 6, "jun": 6, "6": 6,
    "july": 7, "jul": 7, "7": 7,
    "august": 8, "aug": 8, "8": 8,
    "september": 9, "sep": 9, "sept": 9, "9": 9,
    "october": 10, "oct": 10, "10": 10,
    "november": 11, "nov": 11, "11": 11,
    "december": 12, "dec": 12, "12": 12,
}


def get_weekday(day_index, start_day):
    """Calculates the 1-7 (Sun-Sat) weekday integer for any given schedule day."""
    return (start_day - 1 + day_index - 1) % 7 + 1


def decompose_games_to_series(total_games_per_opp):
    """Decompose an even opponent total into matched home-road series pairs."""
    if total_games_per_opp < 4 or total_games_per_opp % 2:
        raise ValueError("Opponent totals must be even and at least four games.")

    three_game_pairs = total_games_per_opp // 6
    if total_games_per_opp - three_game_pairs * 6 == 2:
        three_game_pairs -= 1

    remaining_games = total_games_per_opp - three_game_pairs * 6
    four_game_pairs = remaining_games // 8
    remaining_games -= four_game_pairs * 8
    two_game_pairs = remaining_games // 4
    return [3, 3] * three_game_pairs + [4, 4] * four_game_pairs + [2, 2] * two_game_pairs


def generate_circle_rounds(team_list):
    """Generates intra-group round-robin pairings using the Circle Method."""
    n = len(team_list)
    teams = list(team_list)
    if n % 2 != 0:
        teams.append(None)
        n += 1

    rounds = []
    for _ in range(n - 1):
        pairings = [
            (teams[i], teams[n - 1 - i])
            for i in range(n // 2)
            if teams[i] and teams[n - 1 - i]
        ]
        rounds.append(pairings)
        teams = [teams[0]] + [teams[-1]] + teams[1:-1]
    return rounds


def generate_bipartite_rounds(group_a, group_b):
    """Generates cross-group bipartite pairings with 50/50 home/road balancing."""
    n = len(group_a)
    rounds = []
    for r in range(n):
        pairings = []
        for i in range(n):
            t1 = group_a[i]
            t2 = group_b[(i + r) % n]
            if (i + r) % 2 == 0:
                pairings.append((t1, t2))
            else:
                pairings.append((t2, t1))
        rounds.append(pairings)
    return rounds


def should_swap_series_cycle(cycle_index, series_lengths, max_venue_streak=12):
    """Use venue blocks for equal series when the resulting streak remains allowed."""
    if (
        len(series_lengths) == 4
        and series_lengths == [3, 3, 3, 3]
        and series_lengths[0] * (len(series_lengths) // 2) <= max_venue_streak
    ):
        return cycle_index >= len(series_lengths) // 2
    return cycle_index % 2 == 1


def find_all_valid_distributions(total_games, d_opp, s_opp, i_opp, is_balanced=False):
    """Finds all valid game breakdown configurations (allowing mixed series lengths)."""
    if total_games % 2:
        return []

    valid_sols = []

    # Update range to start at 4 instead of 2 to avoid impossible 1:1 H/A splits
    for g_d in range(4, total_games + 1, 2):
        for g_s in range(4 if s_opp > 0 else 0, total_games + 1, 2 if s_opp > 0 else total_games + 1):
            # --- NEW: Enforce balanced schedule logic ---
            if is_balanced and s_opp > 0 and g_d != g_s:
                continue
            # --------------------------------------------
            used = d_opp * g_d + s_opp * g_s
            rem = total_games - used
            if rem < 0:
                continue

            if i_opp > 0:
                if rem > 0 and rem % i_opp == 0:
                    g_i = rem // i_opp
                    # Enforce minimum 4 games for Interleague to prevent 1-game series logic errors
                    if g_i >= 4 and g_i % 2 == 0:
                        valid_sols.append({
                            "g_div": g_d, "div_total": d_opp * g_d,
                            "g_sub": g_s, "sub_total": s_opp * g_s,
                            "g_inter": g_i, "inter_total": i_opp * g_i,
                            "total_games": total_games,
                            "is_pure_3g": (g_d % 3 == 0 and g_s % 3 == 0 and g_i % 3 == 0)
                        })
            else:
                if rem == 0:
                    valid_sols.append({
                        "g_div": g_d, "div_total": d_opp * g_d,
                        "g_sub": g_s, "sub_total": s_opp * g_s,
                        "g_inter": 0, "inter_total": 0,
                        "total_games": total_games,
                        "is_pure_3g": (g_d % 3 == 0 and g_s % 3 == 0)
                    })

    valid_sols.sort(key=lambda x: (x["g_div"], x["is_pure_3g"], x["g_sub"]), reverse=True)
    return valid_sols

def prompt_user_for_distribution(solutions, d_opp, s_opp, i_opp):
    """Displays formatted breakdown choices and prompts user selection."""
    print("\n" + "=" * 85)
    print(" AVAILABLE GAME DISTRIBUTION BREAKDOWNS (Exact Home / Away Balance)")
    print("=" * 85)
    print(f"{'Opt':<4} | {'Divisional':<23} | {'Subleague Non-Div':<23} | {'Interleague':<23}")
    print("-" * 85)

    for idx, sol in enumerate(solutions, start=1):
        div_series = decompose_games_to_series(sol['g_div'])
        sub_series = decompose_games_to_series(sol['g_sub']) if s_opp > 0 else []
        inter_series = decompose_games_to_series(sol['g_inter']) if i_opp > 0 else []

        div_str = f"{sol['g_div']}g ({sol['div_total']}g) [{len(div_series)} ser]"
        sub_str = f"{sol['g_sub']}g ({sol['sub_total']}g) [{len(sub_series)} ser]" if s_opp > 0 else "N/A"
        inter_str = f"{sol['g_inter']}g ({sol['inter_total']}g) [{len(inter_series)} ser]" if i_opp > 0 else "N/A"

        pure_tag = " *" if sol['is_pure_3g'] else ""
        print(f"{idx:<4} | {div_str:<23} | {sub_str:<23} | {inter_str:<23}{pure_tag}")

    print("=" * 85)
    print(" * Indicates pure 3-game series breakdown. Mixed length used where noted.")

    while True:
        try:
            choice = input(f"Select breakdown option [1-{len(solutions)}] (default 1): ").strip()
            if choice == "":
                return solutions[0]
            val = int(choice)
            if 1 <= val <= len(solutions):
                return solutions[val - 1]
            print(f"Please enter a number between 1 and {len(solutions)}.")
        except ValueError:
            print("Invalid input. Enter an option number.")


def build_dynamic_schedule(
    subleagues, divs_per_sl, teams_per_div, total_games, chosen_sol, interleague=True
):
    team_id = 1
    structure = {}
    for sl in range(1, subleagues + 1):
        structure[sl] = {}
        for div in range(1, divs_per_sl + 1):
            structure[sl][div] = []
            for _ in range(teams_per_div):
                structure[sl][div].append(team_id)
                team_id += 1

    total_teams = team_id - 1

    div_series_lengths = decompose_games_to_series(chosen_sol["g_div"])
    sub_series_lengths = decompose_games_to_series(chosen_sol["g_sub"]) if chosen_sol["g_sub"] > 0 else []
    inter_series_lengths = decompose_games_to_series(chosen_sol["g_inter"]) if chosen_sol["g_inter"] > 0 else []

    div_windows, sub_windows, inter_windows = [], [], []

    if div_series_lengths:
        div_rounds_map = {
            (sl_id, div_id): generate_circle_rounds(div_teams)
            for sl_id, sl in structure.items()
            for div_id, div_teams in sl.items()
        }
        num_div_rounds = len(next(iter(div_rounds_map.values())))

        for cycle_idx, s_len in enumerate(div_series_lengths):
            swap = should_swap_series_cycle(cycle_idx, div_series_lengths)
            for r_idx in range(num_div_rounds):
                window = []
                for (sl_id, div_id), rounds in div_rounds_map.items():
                    for t1, t2 in rounds[r_idx]:
                        window.append({
                            "home": t2 if swap else t1,
                            "away": t1 if swap else t2,
                            "length": s_len,
                        })
                div_windows.append(window)

    if sub_series_lengths and divs_per_sl > 1:
        div_keys = list(structure[1].keys())
        div_pairs = [(div_keys[i], div_keys[j]) for i in range(len(div_keys)) for j in range(i + 1, len(div_keys))]

        for cycle_idx, s_len in enumerate(sub_series_lengths):
            swap = should_swap_series_cycle(cycle_idx, sub_series_lengths)
            for d1_k, d2_k in div_pairs:
                sample_bipartite = generate_bipartite_rounds(structure[1][d1_k], structure[1][d2_k])
                for r_idx in range(len(sample_bipartite)):
                    window = []
                    for sl in structure.values():
                        cr = generate_bipartite_rounds(sl[d1_k], sl[d2_k])
                        for t1, t2 in cr[r_idx]:
                            window.append({
                                "home": t2 if swap else t1,
                                "away": t1 if swap else t2,
                                "length": s_len,
                            })
                    sub_windows.append(window)

    if inter_series_lengths and subleagues > 1:
        sl1_teams = [t for div in structure[1].values() for t in div]
        sl2_teams = [t for div in structure[2].values() for t in div]
        cross_rounds = generate_bipartite_rounds(sl1_teams, sl2_teams)

        for cycle_idx, s_len in enumerate(inter_series_lengths):
            swap = should_swap_series_cycle(cycle_idx, inter_series_lengths)
            for r_idx in range(len(cross_rounds)):
                window = []
                for t1, t2 in cross_rounds[r_idx]:
                    window.append({
                        "home": t2 if swap else t1,
                        "away": t1 if swap else t2,
                        "length": s_len,
                    })
                inter_windows.append(window)

    # ---------------------------------------------------------
    # NEW LOGIC: Anchor Start and End with Divisional Matchups
    # and Proportionally Distribute Remaining Series
    # ---------------------------------------------------------
    windows = []
    
    # Extract the first and last divisional windows to anchor the season
    start_window = div_windows.pop(0) if div_windows else None
    end_window = div_windows.pop(-1) if div_windows else None
    
    # Proportionally space the remaining middle series to prevent clustering
    spread = []
    
    if div_windows:
        for i, w in enumerate(div_windows):
            # Calculate a relative float position between 0.0 and 1.0
            spread.append(((i + 0.5) / len(div_windows), 0, w))
            
    if sub_windows:
        for i, w in enumerate(sub_windows):
            spread.append(((i + 0.5) / len(sub_windows), 1, w))
            
    if inter_windows:
        for i, w in enumerate(inter_windows):
            spread.append(((i + 0.5) / len(inter_windows), 2, w))
            
    # Sort by relative position (and then by source type to break ties consistently)
    spread.sort(key=lambda x: (x[0], x[1]))
    
    # Extract just the window data now that it is evenly sorted
    middle_windows = [item[2] for item in spread]
                
    # Reassemble the season with the divisional anchors
    if start_window:
        windows.append(start_window)
        
    windows.extend(middle_windows)
    
    if end_window:
        windows.append(end_window)

    def covers_all_teams(window):
        return len({
            team
            for series in window
            for team in (series["home"], series["away"])
        }) == total_teams

    if windows:
        first_full_index = next(
            (index for index, window in enumerate(windows) if covers_all_teams(window)),
            None,
        )
        if first_full_index is not None and first_full_index != 0:
            windows[0], windows[first_full_index] = windows[first_full_index], windows[0]

        last_full_index = next(
            (
                index
                for index in range(len(windows) - 1, 0, -1)
                if covers_all_teams(windows[index])
            ),
            None,
        )
        if last_full_index is not None and last_full_index != len(windows) - 1:
            windows[-1], windows[last_full_index] = windows[last_full_index], windows[-1]

    _balance_series_venues(windows, total_teams, total_games)

    return windows, total_teams


def _balance_series_venues(windows, total_teams, total_games):
    """Choose each series venue so every team finishes with equal home and road games."""
    series = [item for window in windows for item in window]
    if total_games % 2:
        raise RuntimeError("Equal season home/away balance requires an even schedule total.")
    series_by_pair = defaultdict(list)
    for item in series:
        series_by_pair[tuple(sorted((item["home"], item["away"])))].append(item["length"])
    if all(
        len(lengths) % 2 == 0 and lengths[::2] == lengths[1::2]
        for lengths in series_by_pair.values()
    ):
        return
    target = total_games // 2
    for seed in range(20):
        rng = random.Random(seed)
        orientation = [rng.randrange(2) for _ in series]
        home_counts = [0] * (total_teams + 1)
        for index, item in enumerate(series):
            home_team = item["home"] if orientation[index] else item["away"]
            home_counts[home_team] += item["length"]

        for _ in range(100000):
            if all(home_counts[team] == target for team in range(1, total_teams + 1)):
                for index, item in enumerate(series):
                    if orientation[index] == 0:
                        item["home"], item["away"] = item["away"], item["home"]
                return
            best_index = None
            best_error = sum((home_counts[team] - target) ** 2 for team in range(1, total_teams + 1))
            for index, item in enumerate(series):
                current_home = item["home"] if orientation[index] else item["away"]
                alternate_home = item["away"] if orientation[index] else item["home"]
                length = item["length"]
                candidate_error = best_error
                current_delta = target - home_counts[current_home]
                alternate_delta = target - home_counts[alternate_home]
                candidate_error += (current_delta + length) ** 2 - current_delta ** 2
                candidate_error += (alternate_delta - length) ** 2 - alternate_delta ** 2
                if candidate_error < best_error:
                    best_error = candidate_error
                    best_index = index
            if best_index is None:
                break
            index = best_index
            item = series[index]
            current_home = item["home"] if orientation[index] else item["away"]
            alternate_home = item["away"] if orientation[index] else item["home"]
            home_counts[current_home] -= item["length"]
            home_counts[alternate_home] += item["length"]
            orientation[index] = 1 - orientation[index]

    raise RuntimeError("Unable to balance series venues across the season.")


def build_team_rest_calendar(
    total_teams,
    games_per_team,
    asg_day,
    asg_before=3,
    asg_after=3,
    season_length=200,
    time_limit_seconds=10,
    random_seed=0,
):
    """Build team-specific rest calendars before assigning individual games."""
    break_start = asg_day - asg_before if asg_day else None
    break_end = asg_day + asg_after if asg_day else None
    model = cp_model.CpModel()
    plays = {
        (team, day): model.NewBoolVar(f"rest_team_{team}_plays_{day}")
        for team in range(1, total_teams + 1)
        for day in range(1, season_length + 1)
    }
    for team in range(1, total_teams + 1):
        model.Add(sum(plays[team, day] for day in range(1, season_length + 1)) == games_per_team)
        model.Add(plays[team, 1] == 1)
        model.Add(plays[team, season_length] == 1)
        for day in range(break_start, break_end + 1) if break_start else []:
            model.Add(plays[team, day] == 0)
        if break_start:
            model.Add(plays[team, break_start - 1] == 1)
            model.Add(plays[team, break_end + 1] == 1)
        for start in range(1, season_length - 5):
            window_days = range(start, start + 7)
            playable_window_days = [
                day
                for day in window_days
                if not (break_start and break_start <= day <= break_end)
            ]
            model.Add(sum(plays[team, day] for day in playable_window_days) >= len(playable_window_days) - 1)

    for day in range(1, season_length + 1):
        if break_start and break_start <= day <= break_end:
            continue
        model.Add(sum(plays[team, day] for team in range(1, total_teams + 1)) >= 1)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    solver.parameters.num_search_workers = CPU_SEARCH_WORKERS
    solver.parameters.random_seed = random_seed
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError(
            f"Unable to build team rest calendars (solver status: {solver.StatusName(status)})."
        )
    return {
        team: {day for day in range(1, season_length + 1) if not solver.Value(plays[team, day])}
        for team in range(1, total_teams + 1)
    }


def assign_games_with_rest_calendar(
    windows,
    total_teams,
    rest_days_by_team,
    season_length,
    asg_day=0,
    asg_before=3,
    asg_after=3,
    retry_count=100,
):
    """Assign matchup games to team rest calendars using bounded matching retries."""
    break_start = asg_day - asg_before if asg_day else None
    break_end = asg_day + asg_after if asg_day else None
    pair_games = defaultdict(list)
    for window in windows:
        for series in window:
            key = tuple(sorted((series["home"], series["away"])))
            length = series["length"]
            for i in range(length):
                pair_games[key].append({
                    "home": series["home"],
                    "away": series["away"],
                    # Last game of a series is the getaway-day game.
                    "time": "1305" if i == length - 1 else "1905",
                })

    for seed in range(retry_count):
        rng = random.Random(seed)
        remaining = {key: list(games) for key, games in pair_games.items()}
        assigned = []
        day = 1
        while day <= season_length:
            if break_start and break_start <= day <= break_end:
                day += 1
                continue
            active = [
                team
                for team in range(1, total_teams + 1)
                if day not in rest_days_by_team[team]
            ]
            graph = nx.Graph()
            graph.add_nodes_from(active)
            future_days = {
                team: {
                    future_day
                    for future_day in range(day, season_length + 1)
                    if not (
                        break_start
                        and break_start <= future_day <= break_end
                    )
                    and future_day not in rest_days_by_team[team]
                }
                for team in active
            }
            for pair, games in remaining.items():
                if games and pair[0] in active and pair[1] in active:
                    common_days = len(future_days[pair[0]] & future_days[pair[1]])
                    weight = 100000 // max(1, common_days) + len(games) * 10 + rng.random()
                    graph.add_edge(*pair, weight=weight)
            for team_a, team_b in nx.max_weight_matching(graph, maxcardinality=True, weight="weight"):
                pair = tuple(sorted((team_a, team_b)))
                assigned.append({**remaining[pair].pop(), "day": day})
            day += 1
        if not any(remaining.values()):
            return sorted(assigned, key=lambda game: (game["day"], game["home"])), asg_day

        tail_start = max(1, season_length - 20)
        tail_games = [game for game in assigned if game["day"] >= tail_start]
        assigned = [game for game in assigned if game["day"] < tail_start]
        for game in tail_games:
            remaining.setdefault(
                tuple(sorted((game["home"], game["away"]))), []
            ).append({"home": game["home"], "away": game["away"]})
        for day in range(tail_start, season_length + 1):
            if break_start and break_start <= day <= break_end:
                continue
            active = [
                team
                for team in range(1, total_teams + 1)
                if day not in rest_days_by_team[team]
            ]
            graph = nx.Graph()
            graph.add_nodes_from(active)
            for pair, games in remaining.items():
                if games and pair[0] in active and pair[1] in active:
                    graph.add_edge(*pair, weight=len(games) * 1000 + rng.random())
            for team_a, team_b in nx.max_weight_matching(graph, maxcardinality=True, weight="weight"):
                pair = tuple(sorted((team_a, team_b)))
                assigned.append({**remaining[pair].pop(), "day": day})
        if not any(remaining.values()):
            return sorted(assigned, key=lambda game: (game["day"], game["home"])), asg_day

    raise RuntimeError("Unable to assign all matchup games to the team rest calendars.")


def assign_games_with_edge_coloring(
    windows,
    total_teams,
    rest_days_by_team,
    season_length,
    asg_day=0,
    asg_before=3,
    asg_after=3,
    node_limit=100000,
):
    """Assign matchup edges to playable days with bounded backtracking."""
    break_start = asg_day - asg_before if asg_day else None
    break_end = asg_day + asg_after if asg_day else None
    edges = []
    for window in windows:
        for series in window:
            pair = tuple(sorted((series["home"], series["away"])))
            edges.extend([pair] * series["length"])

    edge_days = []
    for home, away in edges:
        edge_days.append([
            day
            for day in range(1, season_length + 1)
            if not (break_start and break_start <= day <= break_end)
            and day not in rest_days_by_team[home]
            and day not in rest_days_by_team[away]
        ])
    order = sorted(range(len(edges)), key=lambda index: len(edge_days[index]))
    assigned = {}
    used_by_day = defaultdict(set)
    nodes = 0

    def search(position):
        nonlocal nodes
        nodes += 1
        if nodes > node_limit:
            return False
        if position == len(order):
            return True
        edge_index = order[position]
        home, away = edges[edge_index]
        candidate_days = sorted(
            edge_days[edge_index],
            key=lambda day: len(used_by_day[day]),
        )
        for day in candidate_days:
            if home in used_by_day[day] or away in used_by_day[day]:
                continue
            assigned[edge_index] = day
            used_by_day[day].update((home, away))
            if search(position + 1):
                return True
            used_by_day[day].difference_update((home, away))
            del assigned[edge_index]
        return False

    previous_recursion_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(previous_recursion_limit, len(edges) + 100))
    try:
        solved = search(0)
    finally:
        sys.setrecursionlimit(previous_recursion_limit)
    if not solved:
        raise RuntimeError(f"Unable to edge-color matchup games within {node_limit} search nodes.")
    result = [
        {"home": edges[index][0], "away": edges[index][1], "day": assigned[index]}
        for index in range(len(edges))
    ]
    return sorted(result, key=lambda game: (game["day"], game["home"])), asg_day


def expand_to_game_level_games(
    windows,
    total_teams,
    target_asg_day=0,
    asg_before=3,
    asg_after=3,
    asg_weekday_num=None,
    start_dow=2,
    time_limit_seconds=60,
    enforce_game_streak=True,
    enforce_venue_streak=True,
    enforce_seven_day=True,
    enforce_asg_boundaries=True,
    enforce_league_activity=True,
    rest_days_by_team=None,
    season_length=200,
    max_off_days_in_window=1,
    schedule_allstar_game=False,
):
    """Place individual games with CP-SAT for larger league configurations."""
    team_ids = list(range(1, total_teams + 1))
    total_game_days = sum(max(series["length"] for series in window) for window in windows if window)
    if target_asg_day == 0 and (asg_weekday_num is not None or schedule_allstar_game):
        baseline_games, _ = expand_to_slotted_games(
            windows,
            start_dow=start_dow,
            avoid_league_off_days=True,
            league_off_day_interval=5,
            max_off_days_in_window=max_off_days_in_window,
            schedule_allstar_game=False,
        )
        target_asg_day = max(game["day"] for game in baseline_games) // 2 + 2
    actual_asg_day = target_asg_day
    if asg_weekday_num is not None:
        diff = asg_weekday_num - get_weekday(target_asg_day, start_dow)
        if diff > 3:
            diff -= 7
        elif diff < -3:
            diff += 7
        actual_asg_day += diff

    break_start = actual_asg_day - asg_before if actual_asg_day else None
    break_end = actual_asg_day + asg_after if actual_asg_day else None
    games_per_team = sum(
        series["length"]
        for window in windows
        for series in window
    ) * 2 // total_teams
    # series_id is unique per individual series occurrence (not per window, which can
    # contain many simultaneous series) so consecutive-day constraints can be applied below.
    games = []
    series_id = 0
    for window in windows:
        for series in window:
            for offset in range(series["length"]):
                games.append({
                    "home": series["home"],
                    "away": series["away"],
                    "series_id": series_id,
                    "series_offset": offset,
                })
            series_id += 1

    allowed_days = [
        day
        for day in range(1, season_length + 1)
        if break_start is None or not break_start <= day <= break_end
    ]
    # Single greedy pass reused for both the CP-SAT domain seeding and solver hints
    # (this used to run twice, doubling model-build time for no benefit).
    seed_days = {}
    hint_by_pair = {}
    try:
        seed_games, _ = expand_to_slotted_games(
            windows,
            target_asg_day=target_asg_day,
            asg_before=asg_before,
            asg_after=asg_after,
            asg_weekday_num=asg_weekday_num,
            start_dow=start_dow,
            avoid_league_off_days=False,
        )
        seed_by_pair = {}
        for seed_game in seed_games:
            pair = (seed_game["home"], seed_game["away"])
            seed_by_pair.setdefault(pair, []).append(seed_game["day"])
            hint_by_pair.setdefault(pair, []).append(seed_game["day"])
        seed_positions = {pair: 0 for pair in seed_by_pair}
        for index, game in enumerate(games):
            pair = (game["home"], game["away"])
            position = seed_positions.get(pair, 0)
            if position < len(seed_by_pair.get(pair, [])):
                seed_days[index] = seed_by_pair[pair][position]
                seed_positions[pair] = position + 1
    except RuntimeError:
        seed_days = {}

    model = cp_model.CpModel()
    game_allowed_days = {
        index: [
            day
            for day in allowed_days
            if max_off_days_in_window <= 1
            or index not in seed_days
            or abs(day - seed_days[index]) <= 14
        ]
        for index, game in enumerate(games)
    }
    day_vars = [
        model.NewIntVarFromDomain(
            cp_model.Domain.FromValues(game_allowed_days[index]),
            f"game_day_{index}",
        )
        for index in range(len(games))
    ]
    day_lits = {}
    team_game_indexes = {team: [] for team in range(1, total_teams + 1)}
    pair_game_indexes = {}
    for index, game in enumerate(games):
        team_game_indexes[game["home"]].append(index)
        team_game_indexes[game["away"]].append(index)
        pair_game_indexes.setdefault((game["home"], game["away"]), []).append(index)
        for day in game_allowed_days[index]:
            literal = model.NewBoolVar(f"game_{index}_on_{day}")
            model.Add(day_vars[index] == day).OnlyEnforceIf(literal)
            model.Add(day_vars[index] != day).OnlyEnforceIf(literal.Not())
            day_lits[index, day] = literal

    for team, indexes in team_game_indexes.items():
        team_intervals = [
            model.NewIntervalVar(
                day_vars[index],
                1,
                day_vars[index] + 1,
                f"team_{team}_game_{index}",
            )
            for index in indexes
        ]
        model.AddNoOverlap(team_intervals)

    # Force each series' games onto consecutive calendar days (2/3/4-game series),
    # instead of letting the solver scatter same-matchup games independently.
    series_game_indexes = defaultdict(dict)
    for index, game in enumerate(games):
        series_game_indexes[game["series_id"]][game["series_offset"]] = index
    for offsets in series_game_indexes.values():
        base_index = offsets[0]
        for offset, index in offsets.items():
            if offset == 0:
                continue
            model.Add(day_vars[index] == day_vars[base_index] + offset)

    try:
        pair_positions = {pair: 0 for pair in pair_game_indexes}
        for index, game in enumerate(games):
            pair = (game["home"], game["away"])
            position = pair_positions[pair]
            pair_positions[pair] += 1
            hinted_days = hint_by_pair.get(pair, [])
            if position < len(hinted_days):
                hint_day = hinted_days[position]
                if rest_days_by_team is not None:
                    compatible_days = [
                        day
                        for day in hinted_days[position:]
                        if all(day not in rest_days_by_team[team] for team in (game["home"], game["away"]))
                    ]
                    if compatible_days:
                        hint_day = compatible_days[0]
                if hint_day in allowed_days:
                    model.AddHint(day_vars[index], hint_day)
    except RuntimeError:
        pass

    for indexes in pair_game_indexes.values():
        for day in allowed_days:
            model.Add(
                sum(
                    day_lits[index, day]
                    for index in indexes
                    if (index, day) in day_lits
                )
                <= 1
            )

    plays = {}
    home_plays = {}
    away_plays = {}
    for team, indexes in team_game_indexes.items():
        model.AddAllDifferent([day_vars[index] for index in indexes])
        for day in range(1, season_length + 1):
            team_literals = [day_lits[index, day] for index in indexes if (index, day) in day_lits]
            plays[team, day] = model.NewBoolVar(f"team_{team}_plays_{day}")
            model.Add(sum(team_literals) <= 1)
            model.Add(sum(team_literals) == plays[team, day])
            home_literals = [day_lits[index, day] for index in indexes if games[index]["home"] == team and (index, day) in day_lits]
            away_literals = [day_lits[index, day] for index in indexes if games[index]["away"] == team and (index, day) in day_lits]
            home_plays[team, day] = model.NewBoolVar(f"team_{team}_home_{day}")
            away_plays[team, day] = model.NewBoolVar(f"team_{team}_away_{day}")
            model.Add(sum(home_literals) == home_plays[team, day])
            model.Add(sum(away_literals) == away_plays[team, day])
            model.Add(home_plays[team, day] + away_plays[team, day] == plays[team, day])

        model.Add(plays[team, 1] == 1)
        model.Add(plays[team, season_length] == 1)
        if break_start and enforce_asg_boundaries:
            model.Add(plays[team, break_start - 1] == 1)
            model.Add(plays[team, break_end + 1] == 1)
        off_day_window = 3 if max_off_days_in_window <= 1 else 7
        required_games = 1 if off_day_window == 3 else off_day_window - max_off_days_in_window
        for start in range(1, season_length - off_day_window + 1) if enforce_seven_day else []:
            window_days = range(start, start + off_day_window)
            playable_window_days = [
                day
                for day in window_days
                if not (break_start and break_start <= day <= break_end)
            ]
            model.Add(
                sum(plays[team, day] for day in playable_window_days)
                >= min(required_games, len(playable_window_days))
            )
        for start in range(1, season_length - 20) if enforce_game_streak else []:
            if break_start and start <= break_end and start + 20 >= break_start:
                continue
            model.Add(sum(plays[team, day] for day in range(start, start + 21)) >= 20)
        for start in range(1, season_length - 12) if enforce_venue_streak else []:
            if break_start and start <= break_end and start + 12 >= break_start:
                continue
            model.Add(sum(home_plays[team, day] for day in range(start, start + 13)) <= 12)
            model.Add(sum(away_plays[team, day] for day in range(start, start + 13)) <= 12)

    for day in allowed_days if enforce_league_activity else []:
        model.Add(sum(plays[team, day] for team in team_ids) >= 1)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    solver.parameters.num_search_workers = CPU_SEARCH_WORKERS
    solver.parameters.repair_hint = True
    solver.parameters.hint_conflict_limit = 1000
    solver.parameters.use_lns = True
    solver.parameters.cp_model_presolve = True
    solver.parameters.symmetry_level = 2
    solver.parameters.linearization_level = 0
    solver.parameters.search_branching = cp_model.PORTFOLIO_SEARCH
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError(
            "Unable to produce a game-level schedule satisfying all scheduling rules "
            f"(solver status: {solver.StatusName(status)})."
        )

    for index, game in enumerate(games):
        game["day"] = solver.Value(day_vars[index])
    last_by_series = {}
    for game in games:
        last_by_series[game["series_id"]] = max(last_by_series.get(game["series_id"], 0), game["day"])
    for game in games:
        game["time"] = "1305" if game["day"] == last_by_series[game["series_id"]] else "1905"
        game.pop("series_id", None)
        game.pop("series_offset", None)
    return sorted(games, key=lambda game: (game["day"], game["home"])), actual_asg_day


def _build_conflict_free_rounds(windows, team_ids):
    """Build the series-pairing rounds once; independent of ASG placement so callers can reuse it."""
    rounds = [list(window) for window in windows if window]
    all_series_lengths = {
        series["length"]
        for window in rounds
        for series in window
    }
    compatible_series_schedule = (
        bool(all_series_lengths)
        and max(all_series_lengths) - min(all_series_lengths) <= 1
    )
    mixed_compatible_schedule = compatible_series_schedule and len(all_series_lengths) > 1
    native_rounds_are_valid = all(
        (
            len({team for series in round_series for team in (series["home"], series["away"])})
            == len(team_ids)
            if compatible_series_schedule
            else len({team for series in round_series for team in (series["home"], series["away"])})
            == len(round_series) * 2
        )
        and len({series["length"] for series in round_series}) == 1
        for round_series in rounds
    )

    if not native_rounds_are_valid:
        series_by_pair = {}
        for series in (series for window in windows for series in window):
            key = tuple(sorted((series["home"], series["away"])))
            series_by_pair.setdefault(key, []).append(series)

        rounds = None
        if compatible_series_schedule:
            best_rounds = None
            best_score = None
            for seed in range(100):
                remaining_by_pair = {key: list(series_list) for key, series_list in series_by_pair.items()}
                rng = random.Random(seed)
                candidate_rounds = []
                while any(remaining_by_pair.values()):
                    graph = nx.Graph()
                    graph.add_nodes_from(team_ids)
                    for key, series_list in remaining_by_pair.items():
                        if series_list:
                            graph.add_edge(*key, weight=len(series_list) * 1000 + rng.random())
                    matching = nx.max_weight_matching(graph, maxcardinality=True, weight="weight")
                    if len(matching) != len(team_ids) // 2:
                        break

                    round_series = []
                    for team_a, team_b in matching:
                        key = tuple(sorted((team_a, team_b)))
                        round_series.append(remaining_by_pair[key].pop())
                    candidate_rounds.append(round_series)
                else:
                    team_rounds = {
                        team: [
                            index
                            for index, round_series in enumerate(candidate_rounds)
                            if any(team in (series["home"], series["away"]) for series in round_series)
                        ]
                        for team in team_ids
                    }
                    round_gaps = [
                        current - previous - 1
                        for indices in team_rounds.values()
                        for previous, current in zip(indices, indices[1:])
                    ]
                    score = (
                        max(round_gaps, default=0),
                        sum(round_gaps),
                    )
                    if best_score is None or score < best_score:
                        best_score = score
                        best_rounds = candidate_rounds
            rounds = best_rounds
        else:
            for seed in range(20):
                remaining_by_pair = {key: list(series_list) for key, series_list in series_by_pair.items()}
                rng = random.Random(seed)
                candidate_rounds = []
                while any(remaining_by_pair.values()):
                    graph = nx.Graph()
                    graph.add_nodes_from(team_ids)
                    for key, series_list in remaining_by_pair.items():
                        if series_list:
                            graph.add_edge(*key, weight=len(series_list) * 1000 + rng.random())
                    matching = nx.max_weight_matching(graph, maxcardinality=True, weight="weight")
                    if len(matching) != len(team_ids) // 2:
                        break

                    round_series = []
                    for team_a, team_b in matching:
                        key = tuple(sorted((team_a, team_b)))
                        round_series.append(remaining_by_pair[key].pop())
                    candidate_rounds.append(round_series)
                else:
                    rounds = candidate_rounds
                    break

        if rounds is None:
            raise RuntimeError("Unable to form conflict-free rounds from the remaining series.")

    return rounds, compatible_series_schedule, mixed_compatible_schedule


def _place_series_into_days(
    rounds,
    team_ids,
    compatible_series_schedule,
    mixed_compatible_schedule,
    break_start,
    break_end,
    avoid_league_off_days,
    league_off_day_interval,
    max_off_days_in_window,
    max_consecutive_games,
    max_consecutive_home_or_road,
    rest_window_days=7,
):
    """Place a conflict-free round pairing into calendar days, respecting off-day/ASG-break rules."""
    slotted_games = []
    next_start_day = 1
    consecutive_game_days = 0
    streak_break_interval = min(max_consecutive_games, max_consecutive_home_or_road)
    aligned_asg_prebreak = False
    round_index = 0
    previous_round_length = None
    last_game_day = {team: None for team in team_ids}
    team_streak = {team: 0 for team in team_ids}
    last_venue = {team: None for team in team_ids}
    venue_streak = {team: 0 for team in team_ids}
    last_pair_end = {}
    prebreak_shifted = False
    scheduled_days = {team: set() for team in team_ids}
    last_league_off_day = None

    def has_excess_off_days(days):
        if not days:
            return False
        for start_day in range(1, max(days) - rest_window_days + 2):
            off_days = sum(day not in days for day in range(start_day, start_day + rest_window_days))
            if off_days > max_off_days_in_window:
                return True
        return False

    while round_index < len(rounds):
        round_series = rounds[round_index]
        round_length = max(series["length"] for series in round_series)
        if any(round_length - series["length"] > 1 for series in round_series):
            raise RuntimeError("A round would create more than one consecutive off-day.")

        stagger_round = (
            avoid_league_off_days
            and round_index > 0
            and round_index < len(rounds) - 1
            and (
                (
                    league_off_day_interval is not None
                    and consecutive_game_days + round_length >= league_off_day_interval
                )
                or (
                    league_off_day_interval is None
                    and compatible_series_schedule
                    and not mixed_compatible_schedule
                    and round_index % 2 == 0
                )
                or (
                    league_off_day_interval is None
                    and not compatible_series_schedule
                    and consecutive_game_days + round_length > streak_break_interval
                )
            )
        )
        if break_end is not None and next_start_day == break_end + 1:
            stagger_round = False
        if previous_round_length is not None and previous_round_length != round_length:
            stagger_round = False

        staggered_series = set()
        if (
            break_start is not None
            and break_start - 12 <= next_start_day < break_start
        ):
            stagger_round = False

        if (
            not avoid_league_off_days
            and (
                league_off_day_interval is not None
                and consecutive_game_days + round_length >= league_off_day_interval
                or league_off_day_interval is None
                and consecutive_game_days + round_length > streak_break_interval
            )
        ):
            next_start_day += 1
            consecutive_game_days = 0

        if (
            not aligned_asg_prebreak
            and
            break_start is not None
            and next_start_day < break_start
            and next_start_day + round_length > break_start - 1
        ):
            pre_break_length = break_start - next_start_day
            replacement_candidates = [
                index
                for index in range(round_index + 1, len(rounds))
                if max(series["length"] for series in rounds[index]) <= pre_break_length
            ]
            replacement_index = max(
                replacement_candidates,
                key=lambda index: max(series["length"] for series in rounds[index]),
                default=None,
            )
            if replacement_index is not None:
                rounds[round_index], rounds[replacement_index] = rounds[replacement_index], rounds[round_index]
                chosen_length = max(series["length"] for series in rounds[round_index])
                # Absorb any leftover gap before this round instead of leaving it
                # adjacent to the break, which would violate the no-off-day rule.
                next_start_day += pre_break_length - chosen_length
                aligned_asg_prebreak = True
                continue

        if (
            break_start is not None
            and break_end is not None
            and next_start_day <= break_end
            and next_start_day + round_length - 1 >= break_start
        ):
            next_start_day = break_end + 1
            consecutive_game_days = 0
            stagger_round = False
            staggered_series = set()

        # Force a stagger for any series whose team would otherwise exceed the
        # hard consecutive-games cap (independent of the league off-day heuristic above).
        # Skip the first/last round so the no-off-day-at-season-start/end rule stays intact.
        forced_stagger_indices = set()
        if 0 < round_index < len(rounds) - 1:
            for series_index, series in enumerate(round_series):
                pair = tuple(sorted((series["home"], series["away"])))
                if last_pair_end.get(pair) == next_start_day - 1:
                    forced_stagger_indices.add(series_index)
                    continue
                for team in (series["home"], series["away"]):
                    contiguous = last_game_day[team] is not None and last_game_day[team] + 1 == next_start_day
                    prospective_streak = (
                        team_streak[team] + series["length"] if contiguous else series["length"]
                    )
                    venue = "H" if series["home"] == team else "A"
                    prospective_venue_streak = (
                        venue_streak[team] + series["length"]
                        if contiguous and last_venue[team] == venue
                        else series["length"]
                    )
                    if (
                        prospective_streak > max_consecutive_games
                        or prospective_venue_streak > max_consecutive_home_or_road
                    ):
                        forced_stagger_indices.add(series_index)
                        break
        if forced_stagger_indices:
            stagger_round = True

        stagger_late_first = round_index % 2 == 0
        if stagger_round:
            half_index = len(round_series) // 2
            selected_orientation = None
            for late_first in (stagger_late_first, not stagger_late_first):
                proposed_days = {team: set(days) for team, days in scheduled_days.items()}
                valid_orientation = True
                for series_index, series in enumerate(round_series):
                    stagger = (series_index >= half_index) ^ late_first
                    series_start_day = next_start_day + int(stagger)
                    series_days = range(series_start_day, series_start_day + series["length"])
                    for team in (series["home"], series["away"]):
                        proposed_days[team].update(series_days)
                if any(has_excess_off_days(days) for days in proposed_days.values()):
                    valid_orientation = False
                if valid_orientation:
                    selected_orientation = late_first
                    staggered_series = {
                        index
                        for index in range(len(round_series))
                        if (index >= half_index) ^ late_first
                    }
                    break
            if selected_orientation is None:
                proposed_days = {team: set(days) for team, days in scheduled_days.items()}
                for series_index, series in enumerate(round_series):
                    proposed_days[series["home"]].update(
                        range(next_start_day, next_start_day + series["length"])
                    )
                    proposed_days[series["away"]].update(
                        range(next_start_day, next_start_day + series["length"])
                    )
                for series_index, series in enumerate(round_series):
                    candidate_days = {team: set(days) for team, days in proposed_days.items()}
                    shifted_days = range(
                        next_start_day + 1,
                        next_start_day + series["length"] + 1,
                    )
                    candidate_days[series["home"]].difference_update(
                        range(next_start_day, next_start_day + series["length"])
                    )
                    candidate_days[series["away"]].difference_update(
                        range(next_start_day, next_start_day + series["length"])
                    )
                    candidate_days[series["home"]].update(shifted_days)
                    candidate_days[series["away"]].update(shifted_days)
                    if (
                        not any(has_excess_off_days(days) for days in candidate_days.values())
                        or (
                            break_end is not None
                            and next_start_day > break_end
                            and not staggered_series
                        )
                    ):
                        proposed_days = candidate_days
                        staggered_series.add(series_index)
                        if break_end is not None and next_start_day > break_end:
                            break
                stagger_round = bool(staggered_series)
            else:
                stagger_late_first = selected_orientation

        if forced_stagger_indices:
            staggered_series = staggered_series | forced_stagger_indices
            stagger_round = True

        half_index = len(round_series) // 2
        for series_index, series in enumerate(round_series):
            h, a, length = series["home"], series["away"], series["length"]
            stagger = stagger_round and (
                series_index in staggered_series
                or ((not staggered_series) and ((series_index >= half_index) ^ stagger_late_first))
            )
            series_start_day = next_start_day + int(stagger)
            for day in range(series_start_day, series_start_day + length):
                game_home, game_away = h, a
                slotted_games.append({
                    "day": day,
                    "time": "1905" if day < series_start_day + length - 1 else "1305",
                    "home": game_home,
                    "away": game_away,
                })
            for team in (h, a):
                if last_game_day[team] is not None and last_game_day[team] + 1 == series_start_day:
                    team_streak[team] += length
                else:
                    team_streak[team] = length
                venue = "H" if h == team else "A"
                if last_game_day[team] is not None and last_game_day[team] + 1 == series_start_day and last_venue[team] == venue:
                    venue_streak[team] += length
                else:
                    venue_streak[team] = length
                last_venue[team] = venue
            last_game_day[h] = series_start_day + length - 1
            last_game_day[a] = series_start_day + length - 1
            last_pair_end[tuple(sorted((h, a)))] = series_start_day + length - 1
            scheduled_days[h].update(range(series_start_day, series_start_day + length))
            scheduled_days[a].update(range(series_start_day, series_start_day + length))

        next_start_day += round_length + int(stagger_round)
        consecutive_game_days = 0 if stagger_round else consecutive_game_days + round_length
        previous_round_length = round_length
        round_index += 1

    slotted_games.sort(key=lambda x: (x["day"], x["home"]))

    for team in team_ids:
        team_games = sorted(
            (game["day"], "H" if game["home"] == team else "A")
            for game in slotted_games
            if team in (game["home"], game["away"])
        )
        game_streak = home_streak = road_streak = 0
        previous_day = None
        for day, venue in team_games:
            consecutive_day = previous_day is not None and day == previous_day + 1
            game_streak = game_streak + 1 if consecutive_day else 1
            home_streak = home_streak + 1 if consecutive_day and venue == "H" else int(venue == "H")
            road_streak = road_streak + 1 if consecutive_day and venue == "A" else int(venue == "A")
            if game_streak > max_consecutive_games:
                raise RuntimeError(f"Team {team} exceeds the consecutive-game limit.")
            previous_day = day

    return slotted_games


def expand_to_slotted_games(
    windows,
    target_asg_day=0,
    asg_before=2,
    asg_after=1,
    asg_weekday_num=None,
    start_dow=2,
    max_consecutive_games=20,
    max_consecutive_home_or_road=12,
    avoid_league_off_days=True,
    league_off_day_interval=None,
    max_off_days_in_window=5,
    schedule_allstar_game=False,
    rest_window_days=7,
):
    actual_asg_day = 0
    automatic_asg_day = target_asg_day == 0 and (asg_weekday_num is not None or schedule_allstar_game)

    team_ids = sorted({
        team
        for window in windows
        for series in window
        for team in (series["home"], series["away"])
    })

    # The round pairing is independent of ASG placement, so build it once and
    # reuse it for both the baseline pass (below) and the real placement pass.
    rounds, compatible_series_schedule, mixed_compatible_schedule = _build_conflict_free_rounds(
        windows, team_ids
    )
    flexible_off_day_interval = league_off_day_interval or 7

    if (
        not avoid_league_off_days
        and not automatic_asg_day
        and actual_asg_day == 0
        and len(rounds) > 2
    ):
        minimum_season_days = math.ceil((len(windows) and sum(
            series["length"] for window in windows for series in window
        ) * 2 // len(team_ids)) / 0.85)
        best_flexible_schedule = None
        best_flexible_score = (-1, -1)
        middle_rounds = list(rounds[1:-1])
        for seed in range(25):
            shuffled_rounds = random.Random(seed).sample(middle_rounds, len(middle_rounds))
            candidate_rounds = [rounds[0], *shuffled_rounds, rounds[-1]]
            try:
                candidate_games = _place_series_into_days(
                    list(candidate_rounds), team_ids, compatible_series_schedule,
                    mixed_compatible_schedule, None, None, False, flexible_off_day_interval,
                    max_off_days_in_window, max_consecutive_games,
                    max_consecutive_home_or_road,
                    rest_window_days,
                )
                candidate_games = _insert_budgeted_league_off_days(
                    candidate_games, len(team_ids),
                    len(candidate_games) * 2 // len(team_ids),
                )
            except RuntimeError:
                continue
            max_candidate_day = max(game["day"] for game in candidate_games)
            max_gap = max(
                (
                    current_day - previous_day - 1
                    for team in team_ids
                    for days in [sorted(
                        game["day"] for game in candidate_games
                        if team in (game["home"], game["away"])
                    )]
                    for previous_day, current_day in zip(days, days[1:])
                ),
                default=0,
            )
            max_seven_day_offs = max(
                (
                    sum(day not in team_days for day in range(start, start + 7))
                    for team in team_ids
                    for team_days in [
                        {
                            game["day"] for game in candidate_games
                            if team in (game["home"], game["away"])
                        }
                    ]
                    for start in range(1, max_candidate_day - 5)
                ),
                default=0,
            )
            score = (
                int(max_candidate_day >= minimum_season_days and max_gap <= 1 and max_seven_day_offs <= 1),
                max_candidate_day,
            )
            if max_gap <= 1 and max_seven_day_offs <= 1 and score > best_flexible_score and max_candidate_day <= 200:
                best_flexible_schedule = candidate_games
                best_flexible_score = score
                if score[0]:
                    break
        if best_flexible_schedule is not None:
            return best_flexible_schedule, 0

    if target_asg_day == 0 and (asg_weekday_num is not None or schedule_allstar_game):
        baseline_games = _place_series_into_days(
            list(rounds), team_ids, compatible_series_schedule, mixed_compatible_schedule,
            None, None, avoid_league_off_days, 5, max_off_days_in_window, 20, 12,
            rest_window_days,
        )
        target_asg_day = max(game["day"] for game in baseline_games) // 2 + 2
    if target_asg_day > 0 or asg_weekday_num is not None:
        actual_asg_day = target_asg_day
        if asg_weekday_num is not None:
            diff = asg_weekday_num - get_weekday(target_asg_day, start_dow)
            if diff > 3:
                diff -= 7
            elif diff < -3:
                diff += 7
            actual_asg_day += diff

    break_start = actual_asg_day - asg_before if actual_asg_day > 0 else None
    break_end = actual_asg_day + asg_after if actual_asg_day > 0 else None

    def place_for_asg(candidate_day):
        candidate_start = candidate_day - asg_before if candidate_day > 0 else None
        candidate_end = candidate_day + asg_after if candidate_day > 0 else None
        candidate_games = _place_series_into_days(
            list(rounds), team_ids, compatible_series_schedule, mixed_compatible_schedule,
            candidate_start, candidate_end, avoid_league_off_days, league_off_day_interval,
            max_off_days_in_window, max_consecutive_games, max_consecutive_home_or_road,
            rest_window_days,
        )
        if candidate_day <= 0:
            return candidate_games
        game_days = {game["day"] for game in candidate_games}
        if any(candidate_start <= day <= candidate_end for day in game_days):
            return None
        if any(
            not any(
                game["day"] == boundary_day
                and team in (game["home"], game["away"])
                for game in candidate_games
            )
            for team in team_ids
            for boundary_day in (candidate_start - 1, candidate_end + 1)
        ):
            return None
        return candidate_games

    slotted_games = place_for_asg(actual_asg_day)
    if slotted_games is None and (automatic_asg_day or actual_asg_day > 0):
        for offset in range(1, 15):
            for candidate_day in (target_asg_day - offset, target_asg_day + offset):
                if candidate_day <= asg_before + 1:
                    continue
                if (
                    asg_weekday_num is not None
                    and get_weekday(candidate_day, start_dow) != asg_weekday_num
                ):
                    continue
                try:
                    candidate_games = place_for_asg(candidate_day)
                except RuntimeError:
                    continue
                if candidate_games is not None:
                    actual_asg_day = candidate_day
                    slotted_games = candidate_games
                    break
            if slotted_games is not None:
                break
        else:
            raise RuntimeError("Unable to align the automatic All-Star break with team boundaries.")
    elif slotted_games is None:
        raise RuntimeError("The requested All-Star break cannot satisfy its boundary rules.")

    if avoid_league_off_days and actual_asg_day == 0:
        while True:
            invalid_off_days = get_invalid_league_off_days(slotted_games)
            if not invalid_off_days:
                break
            first_gap = invalid_off_days[0]
            slotted_games = [
                {
                    **game,
                    "day": game["day"] - 1 if game["day"] > first_gap else game["day"],
                }
                for game in slotted_games
            ]
        slotted_games.sort(key=lambda game: (game["day"], game["home"]))
    elif not avoid_league_off_days:
        slotted_games = _insert_budgeted_league_off_days(
            slotted_games,
            len(team_ids),
            len(slotted_games) * 2 // len(team_ids),
            actual_asg_day,
            asg_before,
            asg_after,
        )
        for team in team_ids:
            team_days = sorted(
                game["day"]
                for game in slotted_games
                if team in (game["home"], game["away"])
            )
            if max_off_days_in_window <= 1 and any(
                current_day - previous_day > 2
                for previous_day, current_day in zip(team_days, team_days[1:])
            ):
                raise RuntimeError("Flexible league off-day placement created consecutive team off-days.")
            if max_off_days_in_window <= 1 and any(
                sum(
                    day not in team_days
                    for day in range(start, start + 7)
                ) > 1
                for start in range(1, max(game["day"] for game in slotted_games) - 5)
            ):
                raise RuntimeError("Flexible league off-day placement violated the seven-day rest rule.")

    return slotted_games, actual_asg_day


def generate_html_report(slotted_games, total_teams, html_filename, asg_day=0):
    """Generates a standalone HTML file with a schedule grid and evaluation metrics."""
    if not slotted_games:
        return

    max_day = max(g["day"] for g in slotted_games)
    
    # Initialize data structures
    grid = {t: {d: "" for d in range(1, max_day + 1)} for t in range(1, total_teams + 1)}
    metrics = {t: {"home": 0, "away": 0} for t in range(1, total_teams + 1)}
    
    # Populate data
    for g in slotted_games:
        day, h, a = g["day"], g["home"], g["away"]
        grid[h][day] = f"vs {a}"
        grid[a][day] = f"@ {h}"
        metrics[h]["home"] += 1
        metrics[a]["away"] += 1

    # Build HTML string
    html = [
        "<!DOCTYPE html>",
        "<html><head><title>Schedule Preview</title>",
        "<style>",
        "body { font-family: sans-serif; padding: 20px; color: #333; }",
        ".table-container { overflow: auto; max-width: 100%; max-height: 85vh; border: 1px solid #ccc; }",
        "table { border-collapse: collapse; white-space: nowrap; font-size: 13px; min-width: 100%; }",
        "th, td { border: 1px solid #ddd; padding: 6px 10px; text-align: center; }",
        "th { background-color: #f4f4f4; font-weight: bold; }",
        "th.sticky-top { position: sticky; top: 0; z-index: 2; }",
        "th.sticky-left { position: sticky; left: 0; z-index: 2; }",
        "th.sticky-corner { position: sticky; top: 0; left: 0; z-index: 3; }",
        "td { background-color: #fff; }",
        ".home { color: #2ca02c; font-weight: bold; }", 
        ".away { color: #d62728; font-weight: bold; }",
        ".legend { font-size: 14px; margin-bottom: 12px; }",
        ".asg-row td { background-color: #fdf5d3; color: #b45309; font-weight: bold; letter-spacing: 2px; }",
        ".asg-row th { background-color: #fde047; }",
        "</style></head><body>"
    ]
    
    # --- Evaluation View ---
    html.append("<h2>Schedule Evaluation</h2>")
    html.append("<div style='max-width: 400px;'><table style='min-width: 100%;'>")
    html.append("<tr><th>Team ID</th><th>Home Games</th><th>Away Games</th><th>Total</th></tr>")
    for t in range(1, total_teams + 1):
        total_g = metrics[t]['home'] + metrics[t]['away']
        html.append(f"<tr><th>T{t}</th><td>{metrics[t]['home']}</td><td>{metrics[t]['away']}</td><td>{total_g}</td></tr>")
    html.append("</table></div><br>")
    
    # --- Grid View ---
    html.append("<h2>Schedule Grid</h2>")
    
    # Legend
    html.append("<div class='legend'>")
    html.append("<strong>Legend:</strong> <span class='home'>Green (vs) = Home Game</span> &nbsp;|&nbsp; <span class='away'>Red (@) = Away Game</span>")
    html.append("</div>")
    
    html.append("<div class='table-container'><table>")
    
    # Header Row (Teams on X-Axis)
    html.append("<tr><th class='sticky-corner'>Day</th>")
    for t in range(1, total_teams + 1):
        html.append(f"<th class='sticky-top'>T{t}</th>")
    html.append("</tr>")
    
    # Day Rows (Days on Y-Axis)
    for d in range(1, max_day + 1):
        # NEW: Intercept the loop to draw the ASG Row
        if asg_day > 0 and d == asg_day:
            html.append(f"<tr class='asg-row'><th class='sticky-left'>D{d}</th><td colspan='{total_teams}'>⭐ ALL-STAR GAME ⭐</td></tr>")
            continue

        html.append(f"<tr><th class='sticky-left'>D{d}</th>")
        for t in range(1, total_teams + 1):
            cell = grid[t][d]
            cls = "home" if "vs" in cell else "away" if "@" in cell else ""
            html.append(f"<td class='{cls}'>{cell}</td>")
        html.append("</tr>")
        
    html.append("</table></div>")
    html.append("</body></html>")
    
    with open(html_filename, "w") as f:
        f.write("\n".join(html))


def generate_preview_data(slotted_games, total_teams):
    """Generates evaluation metrics and schedule grid data for frontend JSON consumption."""
    if not slotted_games:
        return {"grid": {}, "metrics": {}, "max_day": 0, "total_teams": total_teams}

    max_day = max(g["day"] for g in slotted_games)
    
    # Initialize data structures
    grid = {str(t): {str(d): "" for d in range(1, max_day + 1)} for t in range(1, total_teams + 1)}
    metrics = {str(t): {"home": 0, "away": 0} for t in range(1, total_teams + 1)}
    
    # Populate data
    for g in slotted_games:
        day, h, a = str(g["day"]), str(g["home"]), str(g["away"])
        grid[h][day] = f"vs {a}"
        grid[a][day] = f"@ {h}"
        metrics[h]["home"] += 1
        metrics[a]["away"] += 1

    return {
        "grid": grid,
        "metrics": metrics,
        "max_day": max_day,
        "total_teams": total_teams
    }


def get_invalid_league_off_days(slotted_games, asg_day=0, asg_before=2, asg_after=1):
    """Return every league-wide off-day outside the scheduled All-Star shutdown."""
    if not slotted_games:
        return []

    last_day = max(game["day"] for game in slotted_games)
    game_days = {game["day"] for game in slotted_games}
    asg_break = (
        set(range(asg_day - asg_before, asg_day + asg_after + 1))
        if asg_day > 0
        else set()
    )
    return sorted(
        day
        for day in range(1, last_day + 1)
        if day not in game_days and day not in asg_break
    )


def _insert_budgeted_league_off_days(
    slotted_games,
    total_teams,
    games_per_team,
    asg_day=0,
    asg_before=2,
    asg_after=1,
):
    """Add shared rest days until the schedule reaches the 15% off-day minimum."""
    minimum_season_days = math.ceil(games_per_team / 0.85)
    if minimum_season_days > 200:
        return slotted_games

    games = list(slotted_games)
    inserted_off_days = []
    while True:
        team_days = {
            team: sorted({game["day"] for game in games if team in (game["home"], game["away"])})
            for team in range(1, total_teams + 1)
        }
        gap_start = None
        for days in team_days.values():
            for previous_day, current_day in zip(days, days[1:]):
                if current_day - previous_day <= 2:
                    continue
                if asg_day and any(
                    asg_day - asg_before <= day <= asg_day + asg_after
                    for day in range(previous_day + 1, current_day)
                ):
                    continue
                gap_start = previous_day + 1
                break
            if gap_start is not None:
                break
        if gap_start is None:
            break
        games = [
            {**game, "day": game["day"] - 1 if game["day"] > gap_start else game["day"]}
            for game in games
        ]

    while max(game["day"] for game in games) < minimum_season_days:
        max_day = max(game["day"] for game in games)
        games_by_day = defaultdict(list)
        for game in games:
            games_by_day[game["day"]].append(game)
        team_days = {
            team: {game["day"] for game in games if team in (game["home"], game["away"])}
            for team in range(1, total_teams + 1)
        }
        candidates = []
        for day in range(2, max_day - 1):
            if day in games_by_day or day - 1 not in games_by_day or day + 1 not in games_by_day:
                continue
            if asg_day and asg_day - asg_before <= day <= asg_day + asg_after:
                continue
            if asg_day and day < asg_day - asg_before and day + 1 >= asg_day - asg_before:
                continue
            if any(abs(day - previous_off_day) < 7 for previous_off_day in inserted_off_days):
                continue
            if any(
                sum(candidate_day not in team_days[team] for candidate_day in range(day - 6, day + 7)) >= 1
                for team in team_days
            ):
                continue
            if all(
                day - 1 in team_days[team]
                and day in team_days[team]
                and day + 1 in team_days[team]
                for team in team_days
            ):
                candidates.append(day)
        if not candidates:
            break
        insertion_day = candidates[-1]
        inserted_off_days.append(insertion_day)
        games = [
            {**game, "day": game["day"] + 1 if game["day"] > insertion_day else game["day"]}
            for game in games
        ]
    return sorted(games, key=lambda game: (game["day"], game["home"]))


def _expand_large_schedule_with_cp_sat(
    windows,
    total_teams,
    target_asg_day=0,
    asg_before=2,
    asg_after=1,
    season_length=200,
    enforce_league_activity=True,
):
    """Place whole series with a compact CP-SAT model for large leagues."""
    series = [
        {
            "home": item["home"],
            "away": item["away"],
            "length": item["length"],
        }
        for window in windows
        for item in window
    ]
    if not series:
        return [], target_asg_day

    break_start = target_asg_day - asg_before if target_asg_day else None
    break_end = target_asg_day + asg_after if target_asg_day else None
    model = cp_model.CpModel()
    starts = []
    ends = []
    team_series = defaultdict(list)
    for index, item in enumerate(series):
        latest_start = season_length - item["length"] + 1
        allowed_starts = [
            day
            for day in range(1, latest_start + 1)
            if break_start is None
            or day + item["length"] - 1 < break_start
            or day > break_end
        ]
        start = model.NewIntVarFromDomain(
            cp_model.Domain.FromValues(allowed_starts), f"series_start_{index}"
        )
        end = model.NewIntVar(item["length"], season_length, f"series_end_{index}")
        model.Add(end == start + item["length"] - 1)
        starts.append(start)
        ends.append(end)
        for team in (item["home"], item["away"]):
            team_series[team].append(index)

    # A team cannot play two series at the same time. The series intervals remain
    # whole and therefore preserve the fixed venue for every game in a series.
    for team, indexes in team_series.items():
        model.AddNoOverlap([
            model.NewIntervalVar(
                starts[index],
                series[index]["length"],
                ends[index] + 1,
                f"team_{team}_series_{index}",
            )
            for index in indexes
        ])

    final_day = model.NewIntVar(1, season_length, "schedule_final_day")
    model.AddMaxEquality(final_day, ends)
    model.Minimize(final_day)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 20
    solver.parameters.num_search_workers = CPU_SEARCH_WORKERS

    for _ in range(40):
        status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            raise RuntimeError(f"Unable to place large-league series (solver status: {solver.StatusName(status)}).")

        repairs = []
        for team, indexes in team_series.items():
            ordered = sorted(indexes, key=lambda index: solver.Value(starts[index]))
            for left, right in zip(ordered, ordered[1:]):
                left_end = solver.Value(ends[left])
                right_start = solver.Value(starts[right])
                gap_start = left_end + 1
                gap_end = right_start - 1
                overlaps_break = (
                    break_start is not None
                    and gap_start <= break_end
                    and gap_end >= break_start
                )
                if gap_end - gap_start + 1 > 1 and not overlaps_break:
                    repairs.append((left, right))
        if not repairs:
            break
        for left, right in repairs:
            model.Add(ends[left] < starts[right])
            model.Add(starts[right] <= ends[left] + 2)
    else:
        raise RuntimeError("Unable to repair large-league team off-day gaps.")

    games = []
    for index, item in enumerate(series):
        start_day = solver.Value(starts[index])
        for offset in range(item["length"]):
            games.append({
                "day": start_day + offset,
                "time": "1305" if offset == item["length"] - 1 else "1905",
                "home": item["home"],
                "away": item["away"],
            })
    return sorted(games, key=lambda game: (game["day"], game["home"])), target_asg_day


def main():
    parser = argparse.ArgumentParser(
        description="OOTP Schedule XML Generator supporting Mixed-Length Series & Interactive Options"
    )
    parser.add_argument("-s", "--subleagues", type=int, default=2)
    parser.add_argument("-d", "--divisions", type=int, default=2)
    parser.add_argument("-t", "--teams-per-div", type=int, default=4)
    parser.add_argument("-g", "--games", type=int, default=162)
    parser.add_argument("-il", "--interleague", type=int, choices=[0, 1], default=None)
    parser.add_argument("-bg", "--balanced", type=int, choices=[0, 1], default=0)
    parser.add_argument("-a", "--allstar-game-day", type=int, default=0, help="Target calendar day for the ASG")
    parser.add_argument("-aw", "--asg-weekday", type=str, default=None, help="Force ASG to fall on this day of the week (e.g., Thursday). Overrides -a.")
    parser.add_argument("-ab", "--asg-before", type=int, default=2)
    parser.add_argument("-aa", "--asg-after", type=int, default=1)
    parser.add_argument("-sdw", "--start-day-of-week", type=str, default="Monday")
    parser.add_argument("-sm", "--start-month", type=str, default="April")
    parser.add_argument("-sd", "--start-day", type=int, default=1)
    parser.add_argument("-o", "--output", type=str, default=None)
    parser.add_argument("--non-interactive", action="store_true", help="Auto-select top breakdown option")
    parser.add_argument(
        "--distribution-option",
        type=int,
        default=None,
        help="Select a displayed game distribution option (1-based)",
    )
    parser.add_argument(
        "--solver-time-limit",
        type=float,
        default=20,
        help="Maximum seconds for the CP-SAT fallback used when the fast matching scheduler fails",
    )

    args = parser.parse_args()

    sdw_num = DAY_MAP.get(str(args.start_day_of_week).lower(), 2)
    sm_num = MONTH_MAP.get(str(args.start_month).lower(), 4)
    sd_num = args.start_day
    
    asg_weekday_num = DAY_MAP.get(str(args.asg_weekday).lower()) if args.asg_weekday else None

    il_flag = str(args.interleague) if args.interleague is not None else ("1" if args.subleagues > 1 else "0")
    bg_flag = str(args.balanced)

    il_str = "ILY" if il_flag == "1" else "ILN"
    bg_str = "BGY" if bg_flag == "1" else "BGN"
    
    il_prefix = f"{il_str}_{bg_str}"

    d_opp = args.teams_per_div - 1
    s_opp = (args.divisions - 1) * args.teams_per_div
    i_opp = (args.subleagues - 1) * args.divisions * args.teams_per_div if il_flag == "1" else 0

    solutions = find_all_valid_distributions(args.games, d_opp, s_opp, i_opp, is_balanced=(bg_flag == "1"))

    if not solutions:
        print(f"Error: No valid game distributions found for {args.games} games.")
        sys.exit(1)

    if args.distribution_option is not None:
        if not 1 <= args.distribution_option <= len(solutions):
            print(f"Error: Distribution option must be between 1 and {len(solutions)}.")
            sys.exit(1)
        chosen_sol = solutions[args.distribution_option - 1]
    elif args.non_interactive or not sys.stdin.isatty():
        chosen_sol = solutions[0]
    else:
        chosen_sol = prompt_user_for_distribution(solutions, d_opp, s_opp, i_opp)

    sl_parts = [f"SL{sl}_" + "_".join([f"D{d}_T{args.teams_per_div}" for d in range(1, args.divisions + 1)]) for sl in range(1, args.subleagues + 1)]
    type_attr = f"{il_prefix}_G{args.games}_" + "_".join(sl_parts)

    # Ensure the assets directory exists
    output_dir = "assets"
    os.makedirs(output_dir, exist_ok=True)

    # Prefix the filename with the assets directory
    base_filename = args.output if args.output else f"{type_attr}.lsdl"
    filename = os.path.join(output_dir, base_filename)

    windows, total_teams = build_dynamic_schedule(
        args.subleagues, args.divisions, args.teams_per_div, args.games, chosen_sol, interleague=(il_flag == "1")
    )

    # Fast path (sub-second): greedy round-based scheduler, which is what already
    # guarantees series-consecutive scheduling. Try it regardless of team count first.
    try:
        slotted_games, final_asg_day = expand_to_slotted_games(
            windows,
            target_asg_day=args.allstar_game_day,
            asg_before=args.asg_before,
            asg_after=args.asg_after,
            asg_weekday_num=asg_weekday_num,
            start_dow=sdw_num,
            avoid_league_off_days=True,
            league_off_day_interval=5,
            max_off_days_in_window=5,
        )
    except RuntimeError as error:
        print(f"Greedy scheduler failed ({error}). Falling back to CP-SAT solver (this can take longer)...")
        rest_season_length = min(200, int(args.games / 0.85 + 0.9999))
        rest_target_day = args.allstar_game_day
        total_window_days = sum(max(series["length"] for series in window) for window in windows if window)
        if rest_target_day == 0 and asg_weekday_num is not None:
            rest_target_day = (total_window_days + total_window_days // 7) // 2
        if asg_weekday_num is not None:
            weekday_adjustment = asg_weekday_num - get_weekday(rest_target_day, sdw_num)
            if weekday_adjustment > 3:
                weekday_adjustment -= 7
            elif weekday_adjustment < -3:
                weekday_adjustment += 7
            rest_target_day += weekday_adjustment
        rest_days_by_team = build_team_rest_calendar(
            total_teams,
            args.games,
            rest_target_day,
            asg_before=args.asg_before,
            asg_after=args.asg_after,
            season_length=rest_season_length,
            time_limit_seconds=min(args.solver_time_limit, 10),
        )
        try:
            slotted_games, final_asg_day = expand_to_game_level_games(
                windows,
                total_teams,
                target_asg_day=args.allstar_game_day,
                asg_before=args.asg_before,
                asg_after=args.asg_after,
                asg_weekday_num=asg_weekday_num,
                start_dow=sdw_num,
                time_limit_seconds=args.solver_time_limit,
                rest_days_by_team=rest_days_by_team,
                season_length=rest_season_length,
                max_off_days_in_window=2,
                enforce_venue_streak=False,
                enforce_game_streak=False,
            )
        except RuntimeError as fallback_error:
            raise RuntimeError(
                "Both the greedy scheduler and the CP-SAT solver failed to produce a schedule for "
                f"this configuration. Last error: {fallback_error}"
            ) from fallback_error

    home_games = defaultdict(int)
    away_games = defaultdict(int)
    for game in slotted_games:
        home_games[game["home"]] += 1
        away_games[game["away"]] += 1
    unbalanced_teams = [
        team
        for team in range(1, total_teams + 1)
        if home_games[team] != away_games[team]
    ]
    if unbalanced_teams:
        raise RuntimeError(
            "Generated schedule violates the equal home/away rule for teams: "
            + ", ".join(map(str, unbalanced_teams))
        )

    root_attrs = {
        "type": type_attr,
        "inter_league": il_flag,
        "balanced_games": bg_flag,
        "games_per_team": str(args.games),
        "start_day_of_week": str(sdw_num),
        "start_month": str(sm_num),
        "start_day": str(sd_num),
    }

    if final_asg_day > 0:
        root_attrs["allstar_game_day"] = str(final_asg_day)

    root = ET.Element("SCHEDULE", **root_attrs)
    games_element = ET.SubElement(root, "GAMES")

    for g in slotted_games:
        ET.SubElement(games_element, "GAME", day=str(g["day"]), time=str(g["time"]), away=str(g["away"]), home=str(g["home"]))

    xmlstr = minidom.parseString(ET.tostring(root)).toprettyxml(indent="  ")
    with open(filename, "w") as f:
        f.write(xmlstr)

    html_filename = filename.replace(".lsdl", ".html")
    generate_html_report(slotted_games, total_teams, html_filename, asg_day=final_asg_day)

    print(f"\nGenerated {len(slotted_games)} total games across {total_teams} teams.")
    if final_asg_day > 0:
        reverse_day_map = {1: "Sunday", 2: "Monday", 3: "Tuesday", 4: "Wednesday", 5: "Thursday", 6: "Friday", 7: "Saturday"}
        actual_dw = reverse_day_map[get_weekday(final_asg_day, sdw_num)]
        print(f"All-Star Game scheduled dynamically for Day {final_asg_day} ({actual_dw})")
    print(f"File saved to: {filename}")


if __name__ == "__main__":
    main()