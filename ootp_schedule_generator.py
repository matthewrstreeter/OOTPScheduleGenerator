import argparse
import os
import sys
import xml.etree.ElementTree as ET
from xml.dom import minidom

DAY_MAP = {
    "sunday": 1, "sun": 1, "1": 1,
    "monday": 2, "mon": 2, "2": 2,
    "tuesday": 3, "tue": 3, "3": 3,
    "wednesday": 4, "wed": 4, "4": 4,
    "thursday": 5, "thu": 5, "5": 5,
    "friday": 6, "fri": 6, "6": 6,
    "saturday": 7, "sat": 7, "7": 7,
}

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


def decompose_games_to_series(total_games_per_opp, allow_uneven=False):
    """Decomposes a game count per opponent into 3-game, 2-game, and 4-game series."""
    if not allow_uneven:
        # --- EXISTING EVEN-SPLIT LOGIC ---
        half_games = total_games_per_opp // 2
        
        rem = half_games
        num_3g = rem // 3
        rem %= 3

        num_2g = 0
        num_4g = 0

        if rem == 1:
            if num_3g > 0:
                num_3g -= 1
                num_4g += 1
            else:
                num_2g += 1
        elif rem == 2:
            num_2g += 1

        spread = []
        for type_idx, count in enumerate([num_3g, num_4g, num_2g]):
            length = [3, 4, 2][type_idx]
            for i in range(count):
                pos = (i + 0.5) / count if count > 0 else 0
                pos += type_idx * 0.0001
                spread.append((pos, [length, length]))
                
        spread.sort(key=lambda x: x[0])
        
        series_list = []
        for pos, pair in spread:
            series_list.extend(pair)

        return series_list
        
    else:
        # --- NEW UNEVEN SPLIT LOGIC ---
        rem = total_games_per_opp
        num_3g = rem // 3
        rem %= 3

        num_2g = 0
        num_4g = 0

        if rem == 1:
            if num_3g > 0:
                num_3g -= 1
                num_4g += 1
            else:
                num_2g += 1 
        elif rem == 2:
            num_2g += 1

        spread = []
        for type_idx, count in enumerate([num_3g, num_4g, num_2g]):
            length = [3, 4, 2][type_idx]
            for i in range(count):
                pos = (i + 0.5) / count if count > 0 else 0
                spread.append((pos + type_idx * 0.001, length))
                
        spread.sort(key=lambda x: x[0])
        
        series_list = []
        for pos, length in spread:
            series_list.append(length)
            
        return series_list


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


def find_all_valid_distributions(total_games, d_opp, s_opp, i_opp, is_balanced=False, allow_uneven=False):
    """Finds all valid game breakdown configurations (allowing mixed series lengths)."""
    valid_sols = []

    d_start, d_step = (3, 1) if allow_uneven else (4, 2)
    s_start, s_step = (3 if s_opp > 0 else 0, 1) if allow_uneven else (4 if s_opp > 0 else 0, 2)
    
    for g_d in range(d_start, total_games + 1, d_step):
        for g_s in range(s_start, total_games + 1, s_step if s_opp > 0 else total_games + 1):
            
            if is_balanced and s_opp > 0 and g_d != g_s:
                continue

            used = d_opp * g_d + s_opp * g_s
            rem = total_games - used
            if rem < 0:
                continue

            if i_opp > 0:
                if rem > 0 and rem % i_opp == 0:
                    g_i = rem // i_opp
                    if g_i >= 4 and g_i % 2 == 0:
                        valid_sols.append({
                            "g_div": g_d, "div_total": d_opp * g_d,
                            "g_sub": g_s, "sub_total": s_opp * g_s,
                            "g_inter": g_i, "inter_total": i_opp * g_i,
                            "total_games": total_games,
                            "is_pure_3g": (g_d % 3 == 0 and g_s % 3 == 0 and g_i % 3 == 0),
                            "allow_uneven": allow_uneven
                        })
            else:
                if rem == 0:
                    valid_sols.append({
                        "g_div": g_d, "div_total": d_opp * g_d,
                        "g_sub": g_s, "sub_total": s_opp * g_s,
                        "g_inter": 0, "inter_total": 0,
                        "total_games": total_games,
                        "is_pure_3g": (g_d % 3 == 0 and g_s % 3 == 0),
                        "allow_uneven": allow_uneven
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
        allow_u = sol.get("allow_uneven", False)
        div_series = decompose_games_to_series(sol['g_div'], allow_uneven=allow_u)
        sub_series = decompose_games_to_series(sol['g_sub'], allow_uneven=allow_u) if s_opp > 0 else []
        inter_series = decompose_games_to_series(sol['g_inter'], allow_uneven=allow_u) if i_opp > 0 else []

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

    allow_u = chosen_sol.get("allow_uneven", False)
    div_series_lengths = decompose_games_to_series(chosen_sol["g_div"], allow_uneven=allow_u)
    sub_series_lengths = decompose_games_to_series(chosen_sol["g_sub"], allow_uneven=allow_u) if chosen_sol["g_sub"] > 0 else []
    inter_series_lengths = decompose_games_to_series(chosen_sol["g_inter"], allow_uneven=allow_u) if chosen_sol["g_inter"] > 0 else []

    div_windows, sub_windows, inter_windows = [], [], []

    if div_series_lengths:
        div_rounds_map = {
            (sl_id, div_id): generate_circle_rounds(div_teams)
            for sl_id, sl in structure.items()
            for div_id, div_teams in sl.items()
        }
        num_div_rounds = len(next(iter(div_rounds_map.values())))

        for cycle_idx, s_len in enumerate(div_series_lengths):
            for r_idx in range(num_div_rounds):
                swap = (cycle_idx + r_idx) % 2 == 1
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
            for d1_k, d2_k in div_pairs:
                sample_bipartite = generate_bipartite_rounds(structure[1][d1_k], structure[1][d2_k])
                for r_idx in range(len(sample_bipartite)):
                    swap = (cycle_idx + r_idx) % 2 == 1
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
            for r_idx in range(len(cross_rounds)):
                swap = (cycle_idx + r_idx) % 2 == 1
                window = []
                for t1, t2 in cross_rounds[r_idx]:
                    window.append({
                        "home": t2 if swap else t1,
                        "away": t1 if swap else t2,
                        "length": s_len,
                    })
                inter_windows.append(window)

    # ---------------------------------------------------------
    # Anchor Start and End with Divisional Matchups
    # and Proportionally Distribute Remaining Series
    # ---------------------------------------------------------
    windows = []
    
    start_window = div_windows.pop(0) if div_windows else None
    end_window = div_windows.pop(-1) if div_windows else None
    
    spread = []
    
    if div_windows:
        for i, w in enumerate(div_windows):
            spread.append(((i + 0.5) / len(div_windows), 0, w))
            
    if sub_windows:
        for i, w in enumerate(sub_windows):
            spread.append(((i + 0.5) / len(sub_windows), 1, w))
            
    if inter_windows:
        for i, w in enumerate(inter_windows):
            spread.append(((i + 0.5) / len(inter_windows), 2, w))
            
    spread.sort(key=lambda x: (x[0], x[1]))
    
    middle_windows = [item[2] for item in spread]
                
    if start_window:
        windows.append(start_window)
        
    windows.extend(middle_windows)
    
    if end_window:
        windows.append(end_window)

    return windows, total_teams


def expand_to_slotted_games(windows, target_asg_day=0, asg_before=2, asg_after=1, asg_weekday_num=None, start_dow=2):
    slotted_games = []
    actual_asg_day = 0
    MIN_SPACING = 7 
    
    if target_asg_day > 0 or asg_weekday_num is not None:
        if target_asg_day == 0:
            total_game_days = sum(max(s["length"] for s in w) if w else 3 for w in windows)
            total_stagger_days = total_game_days // MIN_SPACING 
            target_asg_day = (total_game_days + total_stagger_days) // 2
            
        actual_asg_day = target_asg_day
        if asg_weekday_num is not None:
            diff = asg_weekday_num - get_weekday(target_asg_day, start_dow)
            if diff > 3: diff -= 7
            elif diff < -3: diff += 7
            actual_asg_day += diff
            
        break_start = actual_asg_day - asg_before
        target_first_half_end = break_start - 1
        
        w_idx = 0
        game_days = 0
        while w_idx < len(windows):
            ml = max(s["length"] for s in windows[w_idx]) if windows[w_idx] else 3
            projected_off_days = game_days // MIN_SPACING 
            if game_days + ml + projected_off_days > target_first_half_end:
                break
            game_days += ml
            w_idx += 1
            
        W = w_idx
        off_days_needed = target_first_half_end - game_days
        first_half_off_days = [0] * W
        
        if W > 1 and off_days_needed > 0:
            avail_slots = list(range(1, W - 1))
            if off_days_needed > len(avail_slots):
                avail_slots = list(range(1, W))
            
            n_avail = len(avail_slots)
            num_to_place = min(off_days_needed, n_avail)
            
            selected_indices = set()
            for k in range(num_to_place):
                slot_idx = int(round((k + 0.5) * n_avail / num_to_place - 0.5))
                slot_idx = max(0, min(n_avail - 1, slot_idx))
                while slot_idx in selected_indices and slot_idx < n_avail - 1:
                    slot_idx += 1
                while slot_idx in selected_indices and slot_idx > 0:
                    slot_idx -= 1
                selected_indices.add(slot_idx)
                first_half_off_days[avail_slots[slot_idx]] = 1
    else:
        W = len(windows)
        first_half_off_days = [0] * W
        days_since_last = 0
        for i in range(1, W - 1):
            ml = max(s["length"] for s in windows[i-1]) if windows[i-1] else 3
            days_since_last += ml
            if days_since_last >= MIN_SPACING:
                first_half_off_days[i] = 1
                days_since_last -= MIN_SPACING
        
    day = 1 
    
    # 1. Schedule First Half 
    for i in range(W):
        series_list = windows[i]
        max_len = max(s["length"] for s in series_list) if series_list else 3
        stagger = first_half_off_days[i]
        
        half_idx = len(series_list) // 2
        
        for s_idx, series in enumerate(series_list):
            h, a, length = series["home"], series["away"], series["length"]
            
            in_second_half = (s_idx >= half_idx)
            shift = stagger if (in_second_half ^ (i % 2 == 0)) else 0
            
            start_day = day + shift
            
            for d in range(length):
                slotted_games.append({
                    "day": start_day + d,
                    "time": "1905" if d < length - 1 else "1305",
                    "home": h,
                    "away": a,
                })
        
        day += max_len + stagger
        
    # 2. Apply Strict ASG Break
    if actual_asg_day > 0 and W < len(windows):
        day = actual_asg_day + asg_after + 1
        
    # 3. Schedule Second Half
    if W < len(windows):
        second_half_lengths = [max(s["length"] for s in windows[w]) if windows[w] else 3 for w in range(W, len(windows))]
        SH_W = len(second_half_lengths)
        second_half_off_days = [0] * SH_W
        
        if SH_W > 1:
            allowed_sh_off_days = sum(second_half_lengths) // MIN_SPACING
            sh_avail_slots = list(range(1, SH_W - 1)) if SH_W > 2 else list(range(1, SH_W))
            n_sh_avail = len(sh_avail_slots)
            
            if n_sh_avail > 0 and allowed_sh_off_days > 0:
                num_sh_to_place = min(allowed_sh_off_days, n_sh_avail)
                selected_sh_indices = set()
                
                for k in range(num_sh_to_place):
                    slot_idx = int(round((k + 0.5) * n_sh_avail / num_sh_to_place - 0.5))
                    slot_idx = max(0, min(n_sh_avail - 1, slot_idx))
                    while slot_idx in selected_sh_indices and slot_idx < n_sh_avail - 1:
                        slot_idx += 1
                    while slot_idx in selected_sh_indices and slot_idx > 0:
                        slot_idx -= 1
                    selected_sh_indices.add(slot_idx)
                    second_half_off_days[sh_avail_slots[slot_idx]] = 1
                    
        for idx, i in enumerate(range(W, len(windows))):
            series_list = windows[i]
            max_len = second_half_lengths[idx]
            stagger = second_half_off_days[idx]
            
            half_idx = len(series_list) // 2
            
            for s_idx, series in enumerate(series_list):
                h, a, length = series["home"], series["away"], series["length"]
                
                in_second_half = (s_idx >= half_idx)
                shift = stagger if (in_second_half ^ (idx % 2 == 0)) else 0
                
                start_day = day + shift
                
                for d in range(length):
                    slotted_games.append({
                        "day": start_day + d,
                        "time": "1905" if d < length - 1 else "1305",
                        "home": h,
                        "away": a,
                    })
            
            day += max_len + stagger
            
    return sorted(slotted_games, key=lambda x: (x["day"], x["home"])), actual_asg_day


def generate_preview_data(slotted_games, total_teams):
    """Generates dictionary grid and metrics for schedule preview."""
    if not slotted_games:
        return {"grid": {}, "metrics": {}}

    max_day = max(g["day"] for g in slotted_games)
    
    grid = {t: {d: "" for d in range(1, max_day + 1)} for t in range(1, total_teams + 1)}
    metrics = {t: {"home": 0, "away": 0} for t in range(1, total_teams + 1)}
    
    for g in slotted_games:
        day, h, a = g["day"], g["home"], g["away"]
        grid[h][day] = f"vs {a}"
        grid[a][day] = f"@ {h}"
        metrics[h]["home"] += 1
        metrics[a]["away"] += 1
        
    return {"grid": grid, "metrics": metrics}


def generate_html_report(slotted_games, total_teams, html_filename):
    """Generates a standalone HTML file with a schedule grid and evaluation metrics."""
    if not slotted_games:
        return

    # Fetch grid and metrics using the newly separated function
    preview = generate_preview_data(slotted_games, total_teams)
    grid = preview["grid"]
    metrics = preview["metrics"]
    
    max_day = max(g["day"] for g in slotted_games)

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
        "</style></head><body>"
    ]
    
    html.append("<h2>Schedule Evaluation</h2>")
    html.append("<div style='max-width: 400px;'><table style='min-width: 100%;'>")
    html.append("<tr><th>Team ID</th><th>Home Games</th><th>Away Games</th><th>Total</th></tr>")
    
    for t in range(1, total_teams + 1):
        total_g = metrics[t]['home'] + metrics[t]['away']
        html.append(f"<tr><th>T{t}</th><td>{metrics[t]['home']}</td><td>{metrics[t]['away']}</td><td>{total_g}</td></tr>")
    html.append("</table></div><br>")
    
    html.append("<h2>Schedule Grid</h2>")
    
    html.append("<div class='legend'>")
    html.append("<strong>Legend:</strong> <span class='home'>Green (vs) = Home Game</span> &nbsp;|&nbsp; <span class='away'>Red (@) = Away Game</span>")
    html.append("</div>")
    
    html.append("<div class='table-container'><table>")
    
    html.append("<tr><th class='sticky-corner'>Day</th>")
    for t in range(1, total_teams + 1):
        html.append(f"<th class='sticky-top'>T{t}</th>")
    html.append("</tr>")
    
    for d in range(1, max_day + 1):
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
    parser.add_argument("-u", "--uneven", type=int, choices=[0, 1], default=0, help="Allow uneven home/away splits (e.g., 13-game series)")
    parser.add_argument("-a", "--allstar-game-day", type=int, default=0, help="Target calendar day for the ASG")
    parser.add_argument("-aw", "--asg-weekday", type=str, default=None, help="Force ASG to fall on this day of the week (e.g., Thursday). Overrides -a.")
    parser.add_argument("-ab", "--asg-before", type=int, default=2)
    parser.add_argument("-aa", "--asg-after", type=int, default=1)
    parser.add_argument("-sdw", "--start-day-of-week", type=str, default="Monday")
    parser.add_argument("-sm", "--start-month", type=str, default="April")
    parser.add_argument("-sd", "--start-day", type=int, default=1)
    parser.add_argument("-o", "--output", type=str, default=None)
    parser.add_argument("--non-interactive", action="store_true", help="Auto-select top breakdown option")

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

    solutions = find_all_valid_distributions(args.games, d_opp, s_opp, i_opp, is_balanced=(bg_flag == "1"), allow_uneven=(args.uneven == 1))

    if not solutions:
        print(f"Error: No valid game distributions found for {args.games} games.")
        sys.exit(1)

    if args.non_interactive or not sys.stdin.isatty():
        chosen_sol = solutions[0]
    else:
        chosen_sol = prompt_user_for_distribution(solutions, d_opp, s_opp, i_opp)

    sl_parts = [f"SL{sl}_" + "_".join([f"D{d}_T{args.teams_per_div}" for d in range(1, args.divisions + 1)]) for sl in range(1, args.subleagues + 1)]
    type_attr = f"{il_prefix}_G{args.games}_" + "_".join(sl_parts)

    output_dir = "assets"
    os.makedirs(output_dir, exist_ok=True)

    base_filename = args.output if args.output else f"{type_attr}.lsdl"
    filename = os.path.join(output_dir, base_filename)

    windows, total_teams = build_dynamic_schedule(
        args.subleagues, args.divisions, args.teams_per_div, args.games, chosen_sol, interleague=(il_flag == "1")
    )
    
    slotted_games, final_asg_day = expand_to_slotted_games(
        windows, 
        target_asg_day=args.allstar_game_day, 
        asg_before=args.asg_before, 
        asg_after=args.asg_after,
        asg_weekday_num=asg_weekday_num,
        start_dow=sdw_num
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
    generate_html_report(slotted_games, total_teams, html_filename)

    print(f"\nGenerated {len(slotted_games)} total games across {total_teams} teams.")
    if final_asg_day > 0:
        reverse_day_map = {1: "Sunday", 2: "Monday", 3: "Tuesday", 4: "Wednesday", 5: "Thursday", 6: "Friday", 7: "Saturday"}
        actual_dw = reverse_day_map[get_weekday(final_asg_day, sdw_num)]
        print(f"All-Star Game scheduled dynamically for Day {final_asg_day} ({actual_dw})")
    print(f"File saved to: {filename}")


if __name__ == "__main__":
    main()