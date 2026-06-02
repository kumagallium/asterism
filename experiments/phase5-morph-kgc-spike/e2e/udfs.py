"""設計→Ask 連結スパイクの関数登録。

中身は core の閉じた検証済み関数ライブラリ(csv2rdf.functions)。ここは登録の 1 行だけ。
@udf は Morph-KGC が読み込み時に注入する。csv2rdf は runner が sys.path に通す。
"""

import datetime as _dt

if not hasattr(_dt, "UTC"):  # 3.10 環境向けの無害なシム
    _dt.UTC = _dt.timezone.utc  # noqa: UP017  shim は 3.10 用

from csv2rdf.functions import register

register(udf)  # noqa: F821  ← udf は Morph-KGC が注入
