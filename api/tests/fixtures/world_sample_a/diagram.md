```mermaid
classDiagram
    class Country { schema:name; world:nameJa; world:region; world:regionJa }
    class Observation { world:year; world:population; world:lifeExpectancy; world:fertility }
    Observation --> Country : world:ofCountry
```
