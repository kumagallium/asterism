# Broken AI-generated diagram (regression fixture)

Reproduces the class of `diagram.md` that Mermaid 11 rejects, which rendered as
"Syntax error in text" bomb icons in the catalog / workbench during the
2026-07-08 production dogfood (dataset-9422ba7c). The real file could not be
recovered, so this reconstructs the typical AI mistakes: an illegal class-name
character, a member with a colon, malformed relation arrows, and an unquoted
paren label. None of these is a colon *in a relation label*, so T5 must report a
non-blocking `warn` (not `fail`) — the damage is purely visual.

```mermaid
classDiagram
    direction LR
    class Thermal-Conductivity {
        +value: Float
    }
    class Sample {
        +composition xsd_string
    }
    Sample -> Thermal-Conductivity : measures
    Sample ==> Curve
    Sample --> Curve : has (mW/mK)
```
