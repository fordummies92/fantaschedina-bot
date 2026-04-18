def format_output(schedina: dict, results: list) -> str:
    # Ricava giornata dai risultati
    matchday = next((r.get("matchday") for r in results if r.get("matchday")), None)
    giornata_str = f" — Giornata {matchday}" if matchday else ""

    utente = schedina.get("utente", "")
    utente_str = f"👤 <b>{utente}</b>\n" if utente else ""

    lines = [f"<b>📋 FANTASCHEDINA Serie A{giornata_str}</b>\n{utente_str}"]

    partite_prese = 0
    quota_parziale = 1.0
    partite_finite = 0

    for partita, result in zip(schedina["partite"], results):
        casa = partita["casa"]
        trasferta = partita["trasferta"]
        pronostico = partita["pronostico"]
        quota = partita.get("quota", "?")
        played_date = result.get("played_date", "")

        data_str = f"  <i>({played_date})</i>" if played_date else ""

        if not result.get("found"):
            lines.append(
                f"❓ <b>{casa} - {trasferta}</b>\n"
                f"   Pronostico: <i>{pronostico}</i>\n"
                f"   Risultato: <i>partita non trovata</i>"
            )
            continue

        score = result.get("score", "?")
        correct = result.get("correct")

        if correct is True:
            emoji = "✅"
            partite_prese += 1
            partite_finite += 1
            if isinstance(quota, (int, float)):
                quota_parziale *= quota
        elif correct is False:
            emoji = "❌"
            partite_finite += 1
        else:
            emoji = "⏳"

        lines.append(
            f"{emoji} <b>{casa} - {trasferta}</b>{data_str}\n"
            f"   Pronostico: <i>{pronostico}</i>  →  Risultato: <b>{score}</b>"
        )

    quota_totale = schedina.get("quota_totale", "?")
    totale = len(schedina["partite"])

    lines.append("\n<b>━━━━━━━━━━━━━━━━━━━━━━</b>")
    lines.append(f"<b>🎯 Partite azzeccate:</b>  {partite_prese}/{partite_finite} giocate")
    lines.append(f"<b>💰 Quota schedina:</b>  {quota_totale}")

    if partite_prese > 1:
        lines.append(f"<b>📊 Quota parziale (solo prese):</b>  {quota_parziale:.2f}")

    if partite_finite < totale:
        da_giocare = totale - partite_finite
        lines.append(f"\n<i>⏳ {da_giocare} partite ancora da giocare</i>")

    return "\n".join(lines)
