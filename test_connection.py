# -*- coding: utf-8 -*-
"""Connection test.   py test_connection.py                      -> server date (SZQM0771)
                     py test_connection.py ivu10140 "{\"excg_clsf\":\"0\",\"is_cd\":\"005930\"}"   -> any API with a JSON body"""
import json
import sys

import kb_api as kb


def main():
    api = sys.argv[1] if len(sys.argv) > 1 else "szqm0771"
    body = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    try:
        resp = kb.call(api, body)
    except kb.ApiError as e:
        print("ERROR:", e)
        return 1
    print(json.dumps(resp, ensure_ascii=False, indent=2)[:3000])
    print("\nOK: connection works.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
