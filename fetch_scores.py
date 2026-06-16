def recalculate_fav(record):
    layers = record.get("layers", [])
    if not layers:
        return

    def p(s):
        try: return float(str(s).replace("%",""))
        except: return 0.0

    home_total = 0.0
    away_total = 0.0
    count = 0

    for layer in layers:
        fav_is_home = record.get("favTeam") == record.get("home")
        home_prob = p(layer.get("fav")) if fav_is_home else p(layer.get("und"))
        away_prob = p(layer.get("und")) if fav_is_home else p(layer.get("fav"))
        home_total += home_prob
        away_total += away_prob
        count += 1

    if count == 0:
        return

    avg_home = home_total / count
    avg_away = away_total / count

    # ── FIXED: Direct comparison calculation
    if avg_away > avg_home:
        record["favTeam"] = record.get("away")
        for layer in layers:
            layer["fav"], layer["und"] = layer["und"], layer["fav"]
        log.info("  Corrected favTeam: %s (away) is now FAV over %s (home)",
                 record.get("away"), record.get("home"))
    else:
        record["favTeam"] = record.get("home")
