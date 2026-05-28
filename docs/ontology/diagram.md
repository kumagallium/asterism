# Starrydata Ontology — class diagram

Phase 1 で `csv2rdf-starrydata-{papers,samples,curves}` ingester が出す triple
の構造。`sd:` 接頭辞は `https://kumagallium.github.io/csv2rdf-mcp/starrydata/ontology#`。

```mermaid
classDiagram
    direction LR

    class Paper {
        +dcterms:identifier (SID)
        +schema:identifier (DOI)
        +schema:url
        +schema:name (title)
        +schema:datePublished xsd:date
        +schema:publisher
        +bibo:volume / issue / pages
        +sd:projectName *
        +dcterms:created
    }
    class Sample {
        +dcterms:identifier (sample_id)
        +schema:name (sample_name)
        +sd:compositionString
        +sd:compositionDetails
        +dcterms:created / modified
    }
    class Curve {
        +dcterms:identifier (figure_id)
        +sd:figureName
        +sd:propertyX / propertyY
        +sd:unitXString / unitYString
        +sd:xValuesJSON / yValuesJSON
        +sd:xMin / xMax xsd:double
        +sd:yMin / yMax xsd:double
        +sd:pointCount xsd:integer
        +sd:comments
        +sd:projectName *
    }
    class Descriptor {
        +sd:descriptorName
        +sd:descriptorCategory
        +sd:descriptorComment
        +sd:descriptorExtracted
    }
    class Periodical {
        +schema:name
        +schema:alternateName
    }
    class Person {
        +schema:givenName
        +schema:familyName
    }
    class IngestionActivity {
        +prov:atTime xsd:dateTime
        +prov:endedAtTime xsd:dateTime
        +prov:used (CSV source)
        +prov:wasAssociatedWith (software agent)
    }

    Paper "1" --> "0..n" Person : schema:author
    Paper "1" --> "0..1" Periodical : schema:isPartOf
    Sample "1" --> "1" Paper : sd:fromPaper
    Sample "1" --> "0..n" Descriptor : sd:hasDescriptor
    Curve "1" --> "1" Sample : sd:ofSample
    Paper ..> IngestionActivity : prov:wasGeneratedBy
    Sample ..> IngestionActivity : prov:wasGeneratedBy
    Curve ..> IngestionActivity : prov:wasGeneratedBy

    note for Paper "rdfs:subClassOf schema:ScholarlyArticle, prov:Entity"
    note for Sample "rdfs:subClassOf prov:Entity"
    note for Curve "rdfs:subClassOf prov:Entity\nx/y は方針 C: JSON literal + 集約値"
    note for IngestionActivity "rdfs:subClassOf prov:Activity\n各 ingest 実行で 1 つ発行"
```

## 補足: 配置と接頭辞の対応

| Prefix | URI | クラスの所属 |
|---|---|---|
| `sd:` | `https://kumagallium.github.io/csv2rdf-mcp/starrydata/ontology#` | Paper / Sample / Curve / Descriptor / IngestionActivity |
| `schema:` | `https://schema.org/` | Person / Periodical (reuse) |
| `prov:` | `http://www.w3.org/ns/prov#` | Activity / Entity / Agent (supertype) |
| `dcterms:` | `http://purl.org/dc/terms/` | identifier / created / modified (property reuse) |
| `bibo:` | `http://purl.org/ontology/bibo/` | volume / issue / pages (property reuse) |

詳細な RDFS/OWL 定義は [`starrydata.ttl`](starrydata.ttl)、AI 向け shape は
[`../../data/togomcp/mie/starrydata.yaml`](../../data/togomcp/mie/starrydata.yaml) の `shape_expressions` を参照。

## Phase 1 で意図的に **入れていない**もの (Phase 2 候補)

- **QUDT QuantityKind / Unit**: 現在 `sd:propertyY = "Seebeck coefficient"` のような
  生文字列。Phase 2 で `qudt:SeebeckCoefficient` のような IRI に正規化する予定
- **EMMO / MatOnto / NCIT alignment**: 上位 ontology との `rdfs:subClassOf`
- **sd:DigitizationActivity**: WebPlotDigitizer 由来情報を `prov:wasGeneratedBy`
  経路に乗せる (現在は curves.csv の数値だけを取り込んでいる)
- **`sd:DataPoint` ノード化**: 設計プラン §4 の方針 A (x/y 配列を個別ノード化)。
  Phase 1 は方針 C で済ませている
- **逆方向プロパティ** (`sd:hasSample` 等の inverse): SPARQL 推論で扱えるので
  triple は追加しない
