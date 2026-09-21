import json
import db_utils
from dotenv import load_dotenv
load_dotenv()

def check_stop_loss(account_status):
    try:
        with open('account_status_old.json', 'r') as f:
            account_status_old = json.load(f)
    
        # now check if some keys of the old account status is not present in the actual, if that's true than write the bot_operation as close with reasoning "stoploss"
        old_symbols_list = list(map(lambda x: x['symbol'], account_status_old))
        new_symbols_list = list(map(lambda x: x['symbol'], account_status['open_positions']))

        stop_loss_triggered = []
        for idx, old_symb in enumerate(old_symbols_list):
            if old_symb not in new_symbols_list:
                closed_position_signal = {
                        "operation": "close",
                        "symbol": old_symb,
                        "direction": account_status_old[idx]['side'],
                        "target_portion_of_balance": 1.0,
                        "leverage": 1, # Non applicabile per chiusura
                        "reason": "Stop loss",
                        "stop_loss_percent": 1 # Non applicabile
                    }
                stop_loss_triggered.append({"symbol": old_symb, "direction": account_status_old[idx]['side'], "pnl_usd":account_status_old[idx]['pnl_usd']})
                print(f"ATTENZIONE: Rilevata chiusura posizione esterna per {old_symb}. Registrazione operazione.")
                db_utils.log_bot_operation(closed_position_signal, system_prompt="External closure detected", news_text="")
        return f"{json.dumps(stop_loss_triggered)}"
    except Exception as e:
        # Se il file non esiste o c'è un errore di parsing JSON, significa che non c'erano posizioni precedenti
        # o il file è corrotto. In questo caso, non ci sono stop loss da rilevare.
        print(f"Errore durante la lettura di account_status_old.json: {e}. Nessun SL esterno rilevato.")
        return "[]"