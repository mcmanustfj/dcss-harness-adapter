        // Adapter lobby overlay: explicit unknowns, zero values and report age.
        function field(key, value) {
            entry.find("." + key).text(value == null ? "Unknown" : value);
        }
        field("xl", data.xl);
        field("char", data.char);
        field("place", data.place);
        field("turn", data.turn);
        var duration = data.dur == null ? NaN : Number(data.dur);
        var durationText = "Unknown";
        var durationTitle = "No played-time report received for this connection";
        if (Number.isFinite(duration) && duration >= 0) {
            durationText = duration === 0 ? "0s" : format_duration(duration).text();
            var current = data.turn != null && data.duration_turn != null &&
                String(data.turn) === String(data.duration_turn);
            if (!current)
                durationText += " (last report)";
            durationTitle = "Played time reported by Crawl" +
                (data.duration_turn == null ? "" : " at turn " + data.duration_turn);
        }
        field("dur", durationText);
        entry.find(".dur").attr("title", durationTitle);
        new_list.removeClass("no_game_times");
