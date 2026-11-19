// 生成物。手で直さない。作り直しは `python scripts/gen_relation_catalog_ts.py`（目録の正は
// ingest/src/asterism/relation_catalog.py）。形は `GET /api/relations` の返りと同じ。
// 実行時は API の目録がこれに重なる（relations/catalog.ts）。型（関係名・述語名）はここから導く。

export const BUILTIN_CATALOG = {
  "relations": [
    {
      "name": "sub",
      "label": {
        "ja": "一種",
        "en": "kind of"
      },
      "sentence": {
        "ja": "{s} は {o} の一種",
        "en": "{s} is a kind of {o}"
      },
      "predicates": [
        {
          "name": "subClassOf",
          "iri": "http://www.w3.org/2000/01/rdf-schema#subClassOf",
          "when": [
            "from_kind",
            "class"
          ]
        },
        {
          "name": "subPropertyOf",
          "iri": "http://www.w3.org/2000/01/rdf-schema#subPropertyOf",
          "when": [
            "from_kind",
            "property"
          ]
        }
      ],
      "symmetric_draft": false,
      "symmetric_pair": false,
      "experiment": false,
      "builtin": true,
      "to_property_only": false,
      "same_kind": true,
      "acyclic": true,
      "factor_directions": [],
      "question_op": "kinds",
      "reserve_slug": false,
      "paper": true
    },
    {
      "name": "eq",
      "label": {
        "ja": "同じ",
        "en": "same as"
      },
      "sentence": {
        "ja": "{s} と {o} は同じもの",
        "en": "{s} is the same as {o}"
      },
      "predicates": [
        {
          "name": "equivalentClass",
          "iri": "http://www.w3.org/2002/07/owl#equivalentClass",
          "when": [
            "from_kind",
            "class"
          ]
        },
        {
          "name": "equivalentProperty",
          "iri": "http://www.w3.org/2002/07/owl#equivalentProperty",
          "when": [
            "from_kind",
            "property"
          ]
        }
      ],
      "symmetric_draft": false,
      "symmetric_pair": true,
      "experiment": false,
      "builtin": true,
      "to_property_only": false,
      "same_kind": true,
      "acyclic": false,
      "factor_directions": [],
      "question_op": null,
      "reserve_slug": false,
      "paper": false
    },
    {
      "name": "quantity",
      "label": {
        "ja": "量",
        "en": "quantity"
      },
      "sentence": {
        "ja": "{s} の量は {o}",
        "en": "The quantity of {s} is {o}"
      },
      "predicates": [
        {
          "name": "hasQuantityKind",
          "iri": "http://qudt.org/schema/qudt/hasQuantityKind",
          "when": null
        }
      ],
      "symmetric_draft": false,
      "symmetric_pair": false,
      "experiment": false,
      "builtin": true,
      "to_property_only": false,
      "same_kind": false,
      "acyclic": false,
      "factor_directions": [],
      "question_op": null,
      "reserve_slug": false,
      "paper": false
    },
    {
      "name": "part",
      "label": {
        "ja": "一部",
        "en": "part of"
      },
      "sentence": {
        "ja": "{s} は {o} の一部",
        "en": "{s} is a part of {o}"
      },
      "predicates": [
        {
          "name": "isPartOf",
          "iri": "http://purl.obolibrary.org/obo/BFO_0000050",
          "when": null
        }
      ],
      "symmetric_draft": false,
      "symmetric_pair": false,
      "experiment": false,
      "builtin": true,
      "to_property_only": false,
      "same_kind": true,
      "acyclic": true,
      "factor_directions": [],
      "question_op": "parts",
      "reserve_slug": false,
      "paper": true
    },
    {
      "name": "lowers",
      "label": {
        "ja": "下げる",
        "en": "lowers"
      },
      "sentence": {
        "ja": "{s} は {o} を下げる",
        "en": "{s} lowers {o}"
      },
      "predicates": [
        {
          "name": "lowers",
          "iri": "http://purl.obolibrary.org/obo/RO_0002212",
          "when": null
        }
      ],
      "symmetric_draft": false,
      "symmetric_pair": false,
      "experiment": false,
      "builtin": true,
      "to_property_only": true,
      "same_kind": false,
      "acyclic": false,
      "factor_directions": [
        "lowers",
        "affects"
      ],
      "question_op": "factors",
      "reserve_slug": true,
      "paper": true
    },
    {
      "name": "raises",
      "label": {
        "ja": "上げる",
        "en": "raises"
      },
      "sentence": {
        "ja": "{s} は {o} を上げる",
        "en": "{s} raises {o}"
      },
      "predicates": [
        {
          "name": "raises",
          "iri": "http://purl.obolibrary.org/obo/RO_0002213",
          "when": null
        }
      ],
      "symmetric_draft": false,
      "symmetric_pair": false,
      "experiment": false,
      "builtin": true,
      "to_property_only": true,
      "same_kind": false,
      "acyclic": false,
      "factor_directions": [
        "raises",
        "affects"
      ],
      "question_op": "factors",
      "reserve_slug": true,
      "paper": true
    },
    {
      "name": "affects",
      "label": {
        "ja": "変える",
        "en": "changes"
      },
      "sentence": {
        "ja": "{s} は {o} を変える",
        "en": "{s} changes {o}"
      },
      "predicates": [
        {
          "name": "affects",
          "iri": "http://purl.obolibrary.org/obo/RO_0002211",
          "when": null
        }
      ],
      "symmetric_draft": false,
      "symmetric_pair": false,
      "experiment": false,
      "builtin": true,
      "to_property_only": true,
      "same_kind": false,
      "acyclic": false,
      "factor_directions": [
        "affects"
      ],
      "question_op": "factors",
      "reserve_slug": true,
      "paper": true
    },
    {
      "name": "makes",
      "label": {
        "ja": "作る",
        "en": "makes"
      },
      "sentence": {
        "ja": "{s} で {o} を作る",
        "en": "{s} makes {o}"
      },
      "predicates": [
        {
          "name": "makes",
          "iri": "http://purl.obolibrary.org/obo/RO_0002234",
          "when": null
        }
      ],
      "symmetric_draft": false,
      "symmetric_pair": false,
      "experiment": true,
      "builtin": true,
      "to_property_only": false,
      "same_kind": false,
      "acyclic": false,
      "factor_directions": [],
      "question_op": "makers",
      "reserve_slug": true,
      "paper": true
    },
    {
      "name": "measures",
      "label": {
        "ja": "測る",
        "en": "measures"
      },
      "sentence": {
        "ja": "{s} で {o} を測る",
        "en": "{s} measures {o}"
      },
      "predicates": [
        {
          "name": "measuresProperty",
          "iri": "http://purl.obolibrary.org/obo/RO_0009006",
          "when": [
            "to_kind",
            "property"
          ]
        },
        {
          "name": "measuresMaterial",
          "iri": "http://www.w3.org/ns/sosa/hasFeatureOfInterest",
          "when": [
            "to_kind",
            "class"
          ]
        }
      ],
      "symmetric_draft": false,
      "symmetric_pair": false,
      "experiment": true,
      "builtin": true,
      "to_property_only": false,
      "same_kind": false,
      "acyclic": false,
      "factor_directions": [],
      "question_op": "measurers",
      "reserve_slug": true,
      "paper": true
    },
    {
      "name": "becomes",
      "label": {
        "ja": "変わる",
        "en": "turns into"
      },
      "sentence": {
        "ja": "{s} が {o} に変わる",
        "en": "{s} turns into {o}"
      },
      "predicates": [
        {
          "name": "becomes",
          "iri": "http://purl.obolibrary.org/obo/RO_0001001",
          "when": null
        }
      ],
      "symmetric_draft": false,
      "symmetric_pair": false,
      "experiment": true,
      "builtin": true,
      "to_property_only": false,
      "same_kind": false,
      "acyclic": false,
      "factor_directions": [],
      "question_op": "sources",
      "reserve_slug": true,
      "paper": true
    },
    {
      "name": "competes",
      "label": {
        "ja": "競合する",
        "en": "competes with"
      },
      "sentence": {
        "ja": "{s} と {o} は競合する",
        "en": "{s} competes with {o}"
      },
      "predicates": [
        {
          "name": "competesWith",
          "iri": "https://kumagallium.github.io/asterism/vocab/relation#competesWith",
          "when": null
        }
      ],
      "symmetric_draft": true,
      "symmetric_pair": true,
      "experiment": true,
      "builtin": true,
      "to_property_only": false,
      "same_kind": false,
      "acyclic": false,
      "factor_directions": [],
      "question_op": "competitors",
      "reserve_slug": true,
      "paper": true
    },
    {
      "name": "has_property",
      "label": {
        "ja": "物性",
        "en": "has property"
      },
      "sentence": {
        "ja": "{s} の {o}",
        "en": "{s} has the property {o}"
      },
      "predicates": [
        {
          "name": "hasProperty",
          "iri": "http://purl.obolibrary.org/obo/RO_0000053",
          "when": null
        }
      ],
      "symmetric_draft": false,
      "symmetric_pair": false,
      "experiment": true,
      "builtin": true,
      "to_property_only": false,
      "same_kind": false,
      "acyclic": false,
      "factor_directions": [],
      "question_op": "bearers",
      "reserve_slug": true,
      "paper": true
    }
  ],
  "predicates": [
    {
      "name": "equivalentClass",
      "iri": "http://www.w3.org/2002/07/owl#equivalentClass",
      "shapes": {
        "from": [
          "class"
        ],
        "to": [
          "class"
        ]
      },
      "same_shape": false,
      "roles": null,
      "no_self": false,
      "subsumption": false,
      "symmetric": false,
      "knowledge": false,
      "own": null,
      "cycle_rels": []
    },
    {
      "name": "subClassOf",
      "iri": "http://www.w3.org/2000/01/rdf-schema#subClassOf",
      "shapes": {
        "from": [
          "class"
        ],
        "to": [
          "class"
        ]
      },
      "same_shape": false,
      "roles": null,
      "no_self": false,
      "subsumption": true,
      "symmetric": false,
      "knowledge": false,
      "own": null,
      "cycle_rels": [
        "subClassOf",
        "equivalentClass"
      ]
    },
    {
      "name": "equivalentProperty",
      "iri": "http://www.w3.org/2002/07/owl#equivalentProperty",
      "shapes": {
        "from": [
          "property"
        ],
        "to": [
          "property"
        ]
      },
      "same_shape": false,
      "roles": null,
      "no_self": false,
      "subsumption": false,
      "symmetric": false,
      "knowledge": false,
      "own": null,
      "cycle_rels": []
    },
    {
      "name": "subPropertyOf",
      "iri": "http://www.w3.org/2000/01/rdf-schema#subPropertyOf",
      "shapes": {
        "from": [
          "property"
        ],
        "to": [
          "property"
        ]
      },
      "same_shape": false,
      "roles": null,
      "no_self": false,
      "subsumption": true,
      "symmetric": false,
      "knowledge": false,
      "own": null,
      "cycle_rels": [
        "subPropertyOf",
        "equivalentProperty"
      ]
    },
    {
      "name": "hasQuantityKind",
      "iri": "http://qudt.org/schema/qudt/hasQuantityKind",
      "shapes": {
        "from": [
          "property"
        ],
        "to": []
      },
      "same_shape": false,
      "roles": null,
      "no_self": false,
      "subsumption": false,
      "symmetric": false,
      "knowledge": false,
      "own": null,
      "cycle_rels": []
    },
    {
      "name": "isPartOf",
      "iri": "http://purl.obolibrary.org/obo/BFO_0000050",
      "shapes": {
        "from": [
          "class",
          "property"
        ],
        "to": [
          "class",
          "property"
        ]
      },
      "same_shape": true,
      "roles": null,
      "no_self": false,
      "subsumption": true,
      "symmetric": false,
      "knowledge": true,
      "own": null,
      "cycle_rels": [
        "isPartOf"
      ]
    },
    {
      "name": "lowers",
      "iri": "http://purl.obolibrary.org/obo/RO_0002212",
      "shapes": {
        "from": [
          "class",
          "property"
        ],
        "to": [
          "property"
        ]
      },
      "same_shape": false,
      "roles": null,
      "no_self": false,
      "subsumption": false,
      "symmetric": false,
      "knowledge": true,
      "own": null,
      "cycle_rels": []
    },
    {
      "name": "raises",
      "iri": "http://purl.obolibrary.org/obo/RO_0002213",
      "shapes": {
        "from": [
          "class",
          "property"
        ],
        "to": [
          "property"
        ]
      },
      "same_shape": false,
      "roles": null,
      "no_self": false,
      "subsumption": false,
      "symmetric": false,
      "knowledge": true,
      "own": null,
      "cycle_rels": []
    },
    {
      "name": "affects",
      "iri": "http://purl.obolibrary.org/obo/RO_0002211",
      "shapes": {
        "from": [
          "class",
          "property"
        ],
        "to": [
          "property"
        ]
      },
      "same_shape": false,
      "roles": null,
      "no_self": false,
      "subsumption": false,
      "symmetric": false,
      "knowledge": true,
      "own": null,
      "cycle_rels": []
    },
    {
      "name": "makes",
      "iri": "http://purl.obolibrary.org/obo/RO_0002234",
      "shapes": {
        "from": [
          "class"
        ],
        "to": [
          "class"
        ]
      },
      "same_shape": false,
      "roles": {
        "from": [
          "concept",
          "method",
          "other"
        ],
        "to": [
          "material"
        ]
      },
      "no_self": true,
      "subsumption": false,
      "symmetric": false,
      "knowledge": true,
      "own": null,
      "cycle_rels": []
    },
    {
      "name": "measuresProperty",
      "iri": "http://purl.obolibrary.org/obo/RO_0009006",
      "shapes": {
        "from": [
          "class"
        ],
        "to": [
          "property"
        ]
      },
      "same_shape": false,
      "roles": {
        "from": [
          "concept",
          "method",
          "other"
        ],
        "to": []
      },
      "no_self": true,
      "subsumption": false,
      "symmetric": false,
      "knowledge": true,
      "own": null,
      "cycle_rels": []
    },
    {
      "name": "measuresMaterial",
      "iri": "http://www.w3.org/ns/sosa/hasFeatureOfInterest",
      "shapes": {
        "from": [
          "class"
        ],
        "to": [
          "class"
        ]
      },
      "same_shape": false,
      "roles": {
        "from": [
          "concept",
          "method",
          "other"
        ],
        "to": [
          "material"
        ]
      },
      "no_self": true,
      "subsumption": false,
      "symmetric": false,
      "knowledge": true,
      "own": null,
      "cycle_rels": []
    },
    {
      "name": "becomes",
      "iri": "http://purl.obolibrary.org/obo/RO_0001001",
      "shapes": {
        "from": [
          "class"
        ],
        "to": [
          "class"
        ]
      },
      "same_shape": false,
      "roles": {
        "from": [
          "material"
        ],
        "to": [
          "material"
        ]
      },
      "no_self": true,
      "subsumption": false,
      "symmetric": false,
      "knowledge": true,
      "own": null,
      "cycle_rels": []
    },
    {
      "name": "competesWith",
      "iri": "https://kumagallium.github.io/asterism/vocab/relation#competesWith",
      "shapes": {
        "from": [
          "class"
        ],
        "to": [
          "class"
        ]
      },
      "same_shape": false,
      "roles": {
        "from": [
          "material"
        ],
        "to": [
          "material"
        ]
      },
      "no_self": true,
      "subsumption": false,
      "symmetric": true,
      "knowledge": true,
      "own": {
        "label": {
          "ja": "競合する",
          "en": "competes with"
        },
        "definition": "Two materials (phases) compete: under the same conditions, the formation of one makes the other less likely to form. Symmetric.",
        "searched": [
          "RO (release 2026-09-04)",
          "BFO",
          "SOSA / SSN",
          "OBI",
          "IAO",
          "CHMO",
          "SIO",
          "PATO",
          "ENVO",
          "OLS4 (all ontologies; only schema.org competitor, a sports term)"
        ],
        "date": "2026-11-17"
      },
      "cycle_rels": []
    },
    {
      "name": "hasProperty",
      "iri": "http://purl.obolibrary.org/obo/RO_0000053",
      "shapes": {
        "from": [
          "class"
        ],
        "to": [
          "property"
        ]
      },
      "same_shape": false,
      "roles": {
        "from": [
          "material"
        ],
        "to": []
      },
      "no_self": true,
      "subsumption": false,
      "symmetric": false,
      "knowledge": true,
      "own": null,
      "cycle_rels": []
    }
  ]
} as const
