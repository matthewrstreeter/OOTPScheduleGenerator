# OOTP Schedule Generator

Build realistic custom baseball schedules for your OOTP league with a local Python engine and the polished browser experience at ootpschedules.com.

This project helps league managers generate a full season faster, cleaner, and more realistically than building it by hand.

## Overview

The generator supports multi-subleague, multi-division league setups and focuses on the details that matter most in a real baseball season:

- realistic home and away balance
- calendar-aware spacing
- mixed series lengths
- interleague structure
- All-Star break placement
- visually reviewable season output

Whether you use the local script in this folder or the guided interface on ootpschedules.com, the goal is the same: build a season that feels balanced, credible, and ready for OOTP play.

## Why This Project Exists

Manual OOTP scheduling is time-consuming, and a schedule that looks correct on paper can still be frustrating in practice. This project automates the work that matters most so you can:

- define the league format
- generate a full season structure
- validate balance before import
- avoid building a realistic calendar by hand

The result is a smoother, more practical way to plan a season that feels polished and playable.

## How It Works

### Local Generator

The Python script in this folder is the core scheduling engine. It can generate a full season from league settings including:

- number of subleagues
- number of divisions
- teams per division
- total games per team
- interleague configuration
- season start date and weekday preferences
- All-Star timing preferences

It evaluates valid breakdowns before generating the final schedule, which helps keep the output consistent with the league structure you want.

**Interactive Schedule Breakdown Selection:**

When the script runs in interactive mode, it displays all valid schedule breakdown combinations and prompts the user to choose the option that best fits the league setup.

![Interactive schedule breakdown selection](./schedule_breakdown_selection.png)

This prompt shows the available distribution options, the selected choice, and the generated season summary after the schedule is created.

### Web Experience

The public interface at ootpschedules.com offers the same core logic in a cleaner, browser-based workflow. It is designed for users who want to:

- set up a league quickly
- review the schedule visually
- adjust the season structure with less friction
- generate a season without working in a terminal

This makes the project approachable for both technical and non-technical users.

### Browser Workflow

The screenshots below show the main flow in the public web app from the initial form through schedule generation and saving the downloaded file:

![Initial web form before any changes](./ootpschedules.com-1.png)

![League settings filled in and Find Options selected](./ootpschedules.com-2.png)

![Schedule preview after selecting Preview Schedule Only](./ootpschedules.com-3.png)

![Saving the generated schedule file](./ootpschedules.com-4.png)

## Features

- Multiple subleagues and divisions
- Configurable team counts and league layout
- Adjustable total games per team
- Mixed-length series support, including 2-game, 3-game, and 4-game patterns
- Optional interleague play
- Balanced home and away game totals
- Calendar-aware pacing across the season
- All-Star break placement tied to target timing and weekday preferences
- OOTP-ready XML output
- HTML schedule report for quick visual validation

## Quick Start

Run the generator locally with a standard 162-game league setup:

```bash
python ootp_schedule_generator.py \
  -s 2 \
  -d 2 \
  -t 4 \
  -g 162 \
  -il 1 \
  --non-interactive
```

This creates a full OOTP-style season for a two-subleague, two-division, four-team-per-division league with interleague play enabled.

For a more specific All-Star configuration:

```bash
python ootp_schedule_generator.py \
  -s 2 -d 2 -t 4 -g 162 \
  -a 105 \
  -aw Thursday \
  -ab 3 \
  -aa 3 \
  -sdw Friday \
  -sm April \
  -sd 1
```

This keeps the All-Star break aligned with the season timeline while preserving realistic spacing around the break.

## Command-Line Options

The script supports a range of configuration options for real league setups:

- `-s, --subleagues`: Number of subleagues
- `-d, --divisions`: Number of divisions per subleague
- `-t, --teams-per-div`: Number of teams in each division
- `-g, --games`: Total games per team
- `-il, --interleague`: Enable or disable interleague play
- `-bg, --balanced`: Toggle balanced scheduling behavior in the output
- `-a, --allstar-game-day`: Target day for the All-Star break
- `-aw, --asg-weekday`: Force the break to a preferred weekday
- `-ab, --asg-before`: Reserve time before the break
- `-aa, --asg-after`: Reserve time after the break
- `-sdw, --start-day-of-week`: Weekday the season begins on
- `-sm, --start-month`: Month the season begins in
- `-sd, --start-day`: Day of the month the season begins on
- `-o, --output`: Custom output filename
- `--non-interactive`: Skip the prompt and automatically choose the first valid schedule

## How It Works

Before finalizing a season, the script evaluates the requested league structure against valid distribution patterns and total game counts. It looks for combinations that remain realistic while satisfying the size and format of the league.

That validation step helps keep the generated season aligned with the league setup instead of producing an overly rigid or artificial calendar.

## All-Star Break Handling

The generator can place the All-Star break in a realistic point in the season rather than dropping it in arbitrarily. When a target day or weekday is provided, it tries to anchor the break to a sensible timeframe while preserving spacing around it and keeping the season flow intact.

If both a target day and a preferred weekday are supplied, the weekday setting takes priority.

## Output And Review

The script produces OOTP-ready XML schedule output and also generates an HTML report for visual review. The report helps check:

- home and away balance
- team-by-day structure
- overall schedule quality before import

This makes it easier to review the season quickly and catch issues before using the schedule in OOTP.

## Who This Is For

This project is designed for:

- custom OOTP leagues with multiple divisions and subleagues
- league managers who want realistic pacing instead of rigid templates
- users who want both a local tool and a browser-based interface
- anyone who wants a faster, clearer way to generate and validate a season schedule

## Notes

- Python 3 is required to run the local script.
- No external dependencies are required for the base generator.
- The output is designed for OOTP schedule import and league planning workflows.
- The project is provided as-is and can be adapted to fit custom league formats and season rules.

---

This project is the public-facing home for the OOTP schedule generator and the browser experience at ootpschedules.com. It is built to make custom baseball scheduling faster, more realistic, and easier to manage from start to finish.
