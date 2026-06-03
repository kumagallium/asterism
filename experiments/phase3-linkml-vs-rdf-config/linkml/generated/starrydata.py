# Auto generated from starrydata.yaml by pythongen.py version: 0.0.1
# Generation date: 2026-05-28T18:09:17
# Schema: starrydata
#
# id: https://kumagallium.github.io/asterism/starrydata/ontology
# description: LinkML rewrite of docs/ontology/starrydata.ttl for Phase 3 evaluation. The
#   goal is to determine whether a single model.yaml can replace the four hand-
#   authored artifacts (TBox / Mermaid / MIE shape_expressions / Python class
#   scaffolds) that currently have to be kept in sync.
#
#   Caveats:
#   - LinkML's ShEx generator emits a single shape per class — the MIE
#     shape_expressions block in Phase 1 nested PersonShape inside PaperShape
#     via @<...>, which is also expressible here.
#   - LinkML does not model "composite IRI key" directly. We capture the key
#     composition via `id_prefixes` and a free-form comment instead.
#   - bnode-free is enforced by giving every class an `identifier: true` slot.
#
# license: https://www.apache.org/licenses/LICENSE-2.0

import dataclasses
import re
from dataclasses import dataclass
from datetime import (
    date,
    datetime,
    time
)
from typing import (
    Any,
    ClassVar,
    Dict,
    List,
    Optional,
    Union
)

from jsonasobj2 import (
    JsonObj,
    as_dict
)
from linkml_runtime.linkml_model.meta import (
    EnumDefinition,
    PermissibleValue,
    PvFormulaOptions
)
from linkml_runtime.utils.curienamespace import CurieNamespace
from linkml_runtime.utils.enumerations import EnumDefinitionImpl
from linkml_runtime.utils.formatutils import (
    camelcase,
    sfx,
    underscore
)
from linkml_runtime.utils.metamodelcore import (
    bnode,
    empty_dict,
    empty_list
)
from linkml_runtime.utils.slot import Slot
from linkml_runtime.utils.yamlutils import (
    YAMLRoot,
    extended_float,
    extended_int,
    extended_str
)
from rdflib import (
    Namespace,
    URIRef
)

from linkml_runtime.linkml_model.types import Date, Datetime, Double, Integer, String, Uri
from linkml_runtime.utils.metamodelcore import URI, XSDDate, XSDDateTime

metamodel_version = "1.11.0"
version = "0.1.0"

# Namespaces
BIBO = CurieNamespace('bibo', 'http://purl.org/ontology/bibo/')
DCTERMS = CurieNamespace('dcterms', 'http://purl.org/dc/terms/')
LINKML = CurieNamespace('linkml', 'https://w3id.org/linkml/')
PROV = CurieNamespace('prov', 'http://www.w3.org/ns/prov#')
SCHEMAORG = CurieNamespace('schemaorg', 'https://schema.org/')
SD = CurieNamespace('sd', 'https://kumagallium.github.io/asterism/starrydata/ontology#')
SDR = CurieNamespace('sdr', 'https://kumagallium.github.io/asterism/starrydata/resource/')
XSD = CurieNamespace('xsd', 'http://www.w3.org/2001/XMLSchema#')
DEFAULT_ = SD


# Types

# Class references
class PaperSID(extended_str):
    pass


class SampleSampleCompositeKey(extended_str):
    pass


class CurveCurveCompositeKey(extended_str):
    pass


class DescriptorDescriptorCompositeKey(extended_str):
    pass


class IngestionActivityRunId(extended_str):
    pass


class PersonPersonCompositeKey(extended_str):
    pass


class PeriodicalPeriodicalSlug(extended_str):
    pass


@dataclass(repr=False)
class Paper(YAMLRoot):
    """
    A scholarly article that contains digitized measurement data. Each row
    in starrydata_papers.csv becomes one sd:Paper instance.
    """
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = SD["Paper"]
    class_class_curie: ClassVar[str] = "sd:Paper"
    class_name: ClassVar[str] = "Paper"
    class_model_uri: ClassVar[URIRef] = SD.Paper

    SID: Union[str, PaperSID] = None
    doi: Optional[str] = None
    url: Optional[Union[str, URI]] = None
    title: Optional[str] = None
    datePublished: Optional[Union[str, XSDDate]] = None
    isPartOf: Optional[Union[str, PeriodicalPeriodicalSlug]] = None
    author: Optional[Union[Union[str, PersonPersonCompositeKey], list[Union[str, PersonPersonCompositeKey]]]] = empty_list()
    publisher: Optional[str] = None
    volume: Optional[str] = None
    issue: Optional[str] = None
    pages: Optional[str] = None
    projectName: Optional[Union[str, list[str]]] = empty_list()
    createdAt: Optional[str] = None
    wasGeneratedBy: Optional[Union[str, IngestionActivityRunId]] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self._is_empty(self.SID):
            self.MissingRequiredField("SID")
        if not isinstance(self.SID, PaperSID):
            self.SID = PaperSID(self.SID)

        if self.doi is not None and not isinstance(self.doi, str):
            self.doi = str(self.doi)

        if self.url is not None and not isinstance(self.url, URI):
            self.url = URI(self.url)

        if self.title is not None and not isinstance(self.title, str):
            self.title = str(self.title)

        if self.datePublished is not None and not isinstance(self.datePublished, XSDDate):
            self.datePublished = XSDDate(self.datePublished)

        if self.isPartOf is not None and not isinstance(self.isPartOf, PeriodicalPeriodicalSlug):
            self.isPartOf = PeriodicalPeriodicalSlug(self.isPartOf)

        if not isinstance(self.author, list):
            self.author = [self.author] if self.author is not None else []
        self.author = [v if isinstance(v, PersonPersonCompositeKey) else PersonPersonCompositeKey(v) for v in self.author]

        if self.publisher is not None and not isinstance(self.publisher, str):
            self.publisher = str(self.publisher)

        if self.volume is not None and not isinstance(self.volume, str):
            self.volume = str(self.volume)

        if self.issue is not None and not isinstance(self.issue, str):
            self.issue = str(self.issue)

        if self.pages is not None and not isinstance(self.pages, str):
            self.pages = str(self.pages)

        if not isinstance(self.projectName, list):
            self.projectName = [self.projectName] if self.projectName is not None else []
        self.projectName = [v if isinstance(v, str) else str(v) for v in self.projectName]

        if self.createdAt is not None and not isinstance(self.createdAt, str):
            self.createdAt = str(self.createdAt)

        if self.wasGeneratedBy is not None and not isinstance(self.wasGeneratedBy, IngestionActivityRunId):
            self.wasGeneratedBy = IngestionActivityRunId(self.wasGeneratedBy)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class Sample(YAMLRoot):
    """
    A physical specimen prepared and measured in a paper. Carries a
    composition string and zero or more structured Descriptors.
    """
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = SD["Sample"]
    class_class_curie: ClassVar[str] = "sd:Sample"
    class_name: ClassVar[str] = "Sample"
    class_model_uri: ClassVar[URIRef] = SD.Sample

    sampleCompositeKey: Union[str, SampleSampleCompositeKey] = None
    rawSampleId: str = None
    sampleName: Optional[str] = None
    compositionString: Optional[str] = None
    compositionDetails: Optional[str] = None
    fromPaper: Optional[Union[str, PaperSID]] = None
    hasDescriptor: Optional[Union[Union[str, DescriptorDescriptorCompositeKey], list[Union[str, DescriptorDescriptorCompositeKey]]]] = empty_list()
    createdAt: Optional[str] = None
    modifiedAt: Optional[str] = None
    wasGeneratedBy: Optional[Union[str, IngestionActivityRunId]] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self._is_empty(self.sampleCompositeKey):
            self.MissingRequiredField("sampleCompositeKey")
        if not isinstance(self.sampleCompositeKey, SampleSampleCompositeKey):
            self.sampleCompositeKey = SampleSampleCompositeKey(self.sampleCompositeKey)

        if self._is_empty(self.rawSampleId):
            self.MissingRequiredField("rawSampleId")
        if not isinstance(self.rawSampleId, str):
            self.rawSampleId = str(self.rawSampleId)

        if self.sampleName is not None and not isinstance(self.sampleName, str):
            self.sampleName = str(self.sampleName)

        if self.compositionString is not None and not isinstance(self.compositionString, str):
            self.compositionString = str(self.compositionString)

        if self.compositionDetails is not None and not isinstance(self.compositionDetails, str):
            self.compositionDetails = str(self.compositionDetails)

        if self.fromPaper is not None and not isinstance(self.fromPaper, PaperSID):
            self.fromPaper = PaperSID(self.fromPaper)

        if not isinstance(self.hasDescriptor, list):
            self.hasDescriptor = [self.hasDescriptor] if self.hasDescriptor is not None else []
        self.hasDescriptor = [v if isinstance(v, DescriptorDescriptorCompositeKey) else DescriptorDescriptorCompositeKey(v) for v in self.hasDescriptor]

        if self.createdAt is not None and not isinstance(self.createdAt, str):
            self.createdAt = str(self.createdAt)

        if self.modifiedAt is not None and not isinstance(self.modifiedAt, str):
            self.modifiedAt = str(self.modifiedAt)

        if self.wasGeneratedBy is not None and not isinstance(self.wasGeneratedBy, IngestionActivityRunId):
            self.wasGeneratedBy = IngestionActivityRunId(self.wasGeneratedBy)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class Curve(YAMLRoot):
    """
    A measurement curve digitized from a figure in the source paper. Phase 1
    keeps raw x/y arrays as JSON literals plus pre-computed aggregates
    (xMin/Max/yMin/Max/pointCount) for fast range filtering.
    """
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = SD["Curve"]
    class_class_curie: ClassVar[str] = "sd:Curve"
    class_name: ClassVar[str] = "Curve"
    class_model_uri: ClassVar[URIRef] = SD.Curve

    curveCompositeKey: Union[str, CurveCurveCompositeKey] = None
    rawFigureId: str = None
    figureName: Optional[str] = None
    ofSample: Optional[Union[str, SampleSampleCompositeKey]] = None
    propertyX: Optional[str] = None
    propertyY: Optional[str] = None
    unitXString: Optional[str] = None
    unitYString: Optional[str] = None
    xValuesJSON: Optional[str] = None
    yValuesJSON: Optional[str] = None
    xMin: Optional[float] = None
    xMax: Optional[float] = None
    yMin: Optional[float] = None
    yMax: Optional[float] = None
    pointCount: Optional[int] = None
    comments: Optional[str] = None
    projectName: Optional[Union[str, list[str]]] = empty_list()
    createdAt: Optional[str] = None
    modifiedAt: Optional[str] = None
    wasGeneratedBy: Optional[Union[str, IngestionActivityRunId]] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self._is_empty(self.curveCompositeKey):
            self.MissingRequiredField("curveCompositeKey")
        if not isinstance(self.curveCompositeKey, CurveCurveCompositeKey):
            self.curveCompositeKey = CurveCurveCompositeKey(self.curveCompositeKey)

        if self._is_empty(self.rawFigureId):
            self.MissingRequiredField("rawFigureId")
        if not isinstance(self.rawFigureId, str):
            self.rawFigureId = str(self.rawFigureId)

        if self.figureName is not None and not isinstance(self.figureName, str):
            self.figureName = str(self.figureName)

        if self.ofSample is not None and not isinstance(self.ofSample, SampleSampleCompositeKey):
            self.ofSample = SampleSampleCompositeKey(self.ofSample)

        if self.propertyX is not None and not isinstance(self.propertyX, str):
            self.propertyX = str(self.propertyX)

        if self.propertyY is not None and not isinstance(self.propertyY, str):
            self.propertyY = str(self.propertyY)

        if self.unitXString is not None and not isinstance(self.unitXString, str):
            self.unitXString = str(self.unitXString)

        if self.unitYString is not None and not isinstance(self.unitYString, str):
            self.unitYString = str(self.unitYString)

        if self.xValuesJSON is not None and not isinstance(self.xValuesJSON, str):
            self.xValuesJSON = str(self.xValuesJSON)

        if self.yValuesJSON is not None and not isinstance(self.yValuesJSON, str):
            self.yValuesJSON = str(self.yValuesJSON)

        if self.xMin is not None and not isinstance(self.xMin, float):
            self.xMin = float(self.xMin)

        if self.xMax is not None and not isinstance(self.xMax, float):
            self.xMax = float(self.xMax)

        if self.yMin is not None and not isinstance(self.yMin, float):
            self.yMin = float(self.yMin)

        if self.yMax is not None and not isinstance(self.yMax, float):
            self.yMax = float(self.yMax)

        if self.pointCount is not None and not isinstance(self.pointCount, int):
            self.pointCount = int(self.pointCount)

        if self.comments is not None and not isinstance(self.comments, str):
            self.comments = str(self.comments)

        if not isinstance(self.projectName, list):
            self.projectName = [self.projectName] if self.projectName is not None else []
        self.projectName = [v if isinstance(v, str) else str(v) for v in self.projectName]

        if self.createdAt is not None and not isinstance(self.createdAt, str):
            self.createdAt = str(self.createdAt)

        if self.modifiedAt is not None and not isinstance(self.modifiedAt, str):
            self.modifiedAt = str(self.modifiedAt)

        if self.wasGeneratedBy is not None and not isinstance(self.wasGeneratedBy, IngestionActivityRunId):
            self.wasGeneratedBy = IngestionActivityRunId(self.wasGeneratedBy)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class Descriptor(YAMLRoot):
    """
    Structured metadata extracted from the sample_info JSON column
    (MaterialFamily, Form, FabricationProcess, etc.). Entries with empty
    category/comment/extracted are dropped at ingest time.
    """
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = SD["Descriptor"]
    class_class_curie: ClassVar[str] = "sd:Descriptor"
    class_name: ClassVar[str] = "Descriptor"
    class_model_uri: ClassVar[URIRef] = SD.Descriptor

    descriptorCompositeKey: Union[str, DescriptorDescriptorCompositeKey] = None
    descriptorName: str = None
    descriptorCategory: Optional[str] = None
    descriptorComment: Optional[str] = None
    descriptorExtracted: Optional[str] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self._is_empty(self.descriptorCompositeKey):
            self.MissingRequiredField("descriptorCompositeKey")
        if not isinstance(self.descriptorCompositeKey, DescriptorDescriptorCompositeKey):
            self.descriptorCompositeKey = DescriptorDescriptorCompositeKey(self.descriptorCompositeKey)

        if self._is_empty(self.descriptorName):
            self.MissingRequiredField("descriptorName")
        if not isinstance(self.descriptorName, str):
            self.descriptorName = str(self.descriptorName)

        if self.descriptorCategory is not None and not isinstance(self.descriptorCategory, str):
            self.descriptorCategory = str(self.descriptorCategory)

        if self.descriptorComment is not None and not isinstance(self.descriptorComment, str):
            self.descriptorComment = str(self.descriptorComment)

        if self.descriptorExtracted is not None and not isinstance(self.descriptorExtracted, str):
            self.descriptorExtracted = str(self.descriptorExtracted)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class IngestionActivity(YAMLRoot):
    """
    A single run of the asterism ingester. Each Paper/Sample/Curve
    generated in the run carries prov:wasGeneratedBy → IngestionActivity for
    provenance traceability.
    """
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = SD["IngestionActivity"]
    class_class_curie: ClassVar[str] = "sd:IngestionActivity"
    class_name: ClassVar[str] = "IngestionActivity"
    class_model_uri: ClassVar[URIRef] = SD.IngestionActivity

    runId: Union[str, IngestionActivityRunId] = None
    atTime: Union[str, XSDDateTime] = None
    used: Union[str, URI] = None
    endedAtTime: Optional[Union[str, XSDDateTime]] = None
    wasAssociatedWith: Optional[Union[str, URI]] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self._is_empty(self.runId):
            self.MissingRequiredField("runId")
        if not isinstance(self.runId, IngestionActivityRunId):
            self.runId = IngestionActivityRunId(self.runId)

        if self._is_empty(self.atTime):
            self.MissingRequiredField("atTime")
        if not isinstance(self.atTime, XSDDateTime):
            self.atTime = XSDDateTime(self.atTime)

        if self._is_empty(self.used):
            self.MissingRequiredField("used")
        if not isinstance(self.used, URI):
            self.used = URI(self.used)

        if self.endedAtTime is not None and not isinstance(self.endedAtTime, XSDDateTime):
            self.endedAtTime = XSDDateTime(self.endedAtTime)

        if self.wasAssociatedWith is not None and not isinstance(self.wasAssociatedWith, URI):
            self.wasAssociatedWith = URI(self.wasAssociatedWith)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class Person(YAMLRoot):
    """
    An author or other PROV-O agent linked to a paper.
    """
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = SCHEMAORG["Person"]
    class_class_curie: ClassVar[str] = "schemaorg:Person"
    class_name: ClassVar[str] = "Person"
    class_model_uri: ClassVar[URIRef] = SD.Person

    personCompositeKey: Union[str, PersonPersonCompositeKey] = None
    givenName: Optional[str] = None
    familyName: Optional[str] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self._is_empty(self.personCompositeKey):
            self.MissingRequiredField("personCompositeKey")
        if not isinstance(self.personCompositeKey, PersonPersonCompositeKey):
            self.personCompositeKey = PersonPersonCompositeKey(self.personCompositeKey)

        if self.givenName is not None and not isinstance(self.givenName, str):
            self.givenName = str(self.givenName)

        if self.familyName is not None and not isinstance(self.familyName, str):
            self.familyName = str(self.familyName)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class Periodical(YAMLRoot):
    """
    A journal / book series the paper appears in.
    """
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = SCHEMAORG["Periodical"]
    class_class_curie: ClassVar[str] = "schemaorg:Periodical"
    class_name: ClassVar[str] = "Periodical"
    class_model_uri: ClassVar[URIRef] = SD.Periodical

    periodicalSlug: Union[str, PeriodicalPeriodicalSlug] = None
    periodicalName: Optional[str] = None
    alternateName: Optional[str] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self._is_empty(self.periodicalSlug):
            self.MissingRequiredField("periodicalSlug")
        if not isinstance(self.periodicalSlug, PeriodicalPeriodicalSlug):
            self.periodicalSlug = PeriodicalPeriodicalSlug(self.periodicalSlug)

        if self.periodicalName is not None and not isinstance(self.periodicalName, str):
            self.periodicalName = str(self.periodicalName)

        if self.alternateName is not None and not isinstance(self.alternateName, str):
            self.alternateName = str(self.alternateName)

        super().__post_init__(**kwargs)


@dataclass(repr=False)
class HasIngestionProvenance(YAMLRoot):
    """
    Marker mixin: any Paper/Sample/Curve gets prov:wasGeneratedBy pointing
    at the IngestionActivity that emitted it. Concrete classes inherit the
    `wasGeneratedBy` slot.
    """
    _inherited_slots: ClassVar[list[str]] = []

    class_class_uri: ClassVar[URIRef] = SD["HasIngestionProvenance"]
    class_class_curie: ClassVar[str] = "sd:HasIngestionProvenance"
    class_name: ClassVar[str] = "HasIngestionProvenance"
    class_model_uri: ClassVar[URIRef] = SD.HasIngestionProvenance

    wasGeneratedBy: Optional[Union[str, IngestionActivityRunId]] = None

    def __post_init__(self, *_: str, **kwargs: Any):
        if self.wasGeneratedBy is not None and not isinstance(self.wasGeneratedBy, IngestionActivityRunId):
            self.wasGeneratedBy = IngestionActivityRunId(self.wasGeneratedBy)

        super().__post_init__(**kwargs)


# Enumerations


# Slots
class slots:
    pass

slots.SID = Slot(uri=DCTERMS.identifier, name="SID", curie=DCTERMS.curie('identifier'),
                   model_uri=SD.SID, domain=None, range=str)

slots.doi = Slot(uri=SCHEMAORG.identifier, name="doi", curie=SCHEMAORG.curie('identifier'),
                   model_uri=SD.doi, domain=None, range=Optional[str])

slots.url = Slot(uri=SCHEMAORG.url, name="url", curie=SCHEMAORG.curie('url'),
                   model_uri=SD.url, domain=None, range=Optional[Union[str, URI]])

slots.title = Slot(uri=SCHEMAORG.name, name="title", curie=SCHEMAORG.curie('name'),
                   model_uri=SD.title, domain=None, range=Optional[str])

slots.datePublished = Slot(uri=SCHEMAORG.datePublished, name="datePublished", curie=SCHEMAORG.curie('datePublished'),
                   model_uri=SD.datePublished, domain=None, range=Optional[Union[str, XSDDate]])

slots.isPartOf = Slot(uri=SCHEMAORG.isPartOf, name="isPartOf", curie=SCHEMAORG.curie('isPartOf'),
                   model_uri=SD.isPartOf, domain=None, range=Optional[Union[str, PeriodicalPeriodicalSlug]])

slots.author = Slot(uri=SCHEMAORG.author, name="author", curie=SCHEMAORG.curie('author'),
                   model_uri=SD.author, domain=None, range=Optional[Union[Union[str, PersonPersonCompositeKey], list[Union[str, PersonPersonCompositeKey]]]])

slots.publisher = Slot(uri=SCHEMAORG.publisher, name="publisher", curie=SCHEMAORG.curie('publisher'),
                   model_uri=SD.publisher, domain=None, range=Optional[str])

slots.volume = Slot(uri=BIBO.volume, name="volume", curie=BIBO.curie('volume'),
                   model_uri=SD.volume, domain=None, range=Optional[str])

slots.issue = Slot(uri=BIBO.issue, name="issue", curie=BIBO.curie('issue'),
                   model_uri=SD.issue, domain=None, range=Optional[str])

slots.pages = Slot(uri=BIBO.pages, name="pages", curie=BIBO.curie('pages'),
                   model_uri=SD.pages, domain=None, range=Optional[str])

slots.projectName = Slot(uri=SD.projectName, name="projectName", curie=SD.curie('projectName'),
                   model_uri=SD.projectName, domain=None, range=Optional[Union[str, list[str]]])

slots.createdAt = Slot(uri=DCTERMS.created, name="createdAt", curie=DCTERMS.curie('created'),
                   model_uri=SD.createdAt, domain=None, range=Optional[str])

slots.modifiedAt = Slot(uri=DCTERMS.modified, name="modifiedAt", curie=DCTERMS.curie('modified'),
                   model_uri=SD.modifiedAt, domain=None, range=Optional[str])

slots.sampleCompositeKey = Slot(uri=DCTERMS.identifier, name="sampleCompositeKey", curie=DCTERMS.curie('identifier'),
                   model_uri=SD.sampleCompositeKey, domain=None, range=str)

slots.rawSampleId = Slot(uri=SD.rawSampleId, name="rawSampleId", curie=SD.curie('rawSampleId'),
                   model_uri=SD.rawSampleId, domain=None, range=str)

slots.sampleName = Slot(uri=SCHEMAORG.name, name="sampleName", curie=SCHEMAORG.curie('name'),
                   model_uri=SD.sampleName, domain=None, range=Optional[str])

slots.compositionString = Slot(uri=SD.compositionString, name="compositionString", curie=SD.curie('compositionString'),
                   model_uri=SD.compositionString, domain=None, range=Optional[str])

slots.compositionDetails = Slot(uri=SD.compositionDetails, name="compositionDetails", curie=SD.curie('compositionDetails'),
                   model_uri=SD.compositionDetails, domain=None, range=Optional[str])

slots.fromPaper = Slot(uri=SD.fromPaper, name="fromPaper", curie=SD.curie('fromPaper'),
                   model_uri=SD.fromPaper, domain=None, range=Optional[Union[str, PaperSID]])

slots.hasDescriptor = Slot(uri=SD.hasDescriptor, name="hasDescriptor", curie=SD.curie('hasDescriptor'),
                   model_uri=SD.hasDescriptor, domain=None, range=Optional[Union[Union[str, DescriptorDescriptorCompositeKey], list[Union[str, DescriptorDescriptorCompositeKey]]]])

slots.descriptorCompositeKey = Slot(uri=DCTERMS.identifier, name="descriptorCompositeKey", curie=DCTERMS.curie('identifier'),
                   model_uri=SD.descriptorCompositeKey, domain=None, range=str)

slots.descriptorName = Slot(uri=SD.descriptorName, name="descriptorName", curie=SD.curie('descriptorName'),
                   model_uri=SD.descriptorName, domain=None, range=str)

slots.descriptorCategory = Slot(uri=SD.descriptorCategory, name="descriptorCategory", curie=SD.curie('descriptorCategory'),
                   model_uri=SD.descriptorCategory, domain=None, range=Optional[str])

slots.descriptorComment = Slot(uri=SD.descriptorComment, name="descriptorComment", curie=SD.curie('descriptorComment'),
                   model_uri=SD.descriptorComment, domain=None, range=Optional[str])

slots.descriptorExtracted = Slot(uri=SD.descriptorExtracted, name="descriptorExtracted", curie=SD.curie('descriptorExtracted'),
                   model_uri=SD.descriptorExtracted, domain=None, range=Optional[str])

slots.curveCompositeKey = Slot(uri=DCTERMS.identifier, name="curveCompositeKey", curie=DCTERMS.curie('identifier'),
                   model_uri=SD.curveCompositeKey, domain=None, range=str)

slots.rawFigureId = Slot(uri=SD.rawFigureId, name="rawFigureId", curie=SD.curie('rawFigureId'),
                   model_uri=SD.rawFigureId, domain=None, range=str)

slots.figureName = Slot(uri=SD.figureName, name="figureName", curie=SD.curie('figureName'),
                   model_uri=SD.figureName, domain=None, range=Optional[str])

slots.ofSample = Slot(uri=SD.ofSample, name="ofSample", curie=SD.curie('ofSample'),
                   model_uri=SD.ofSample, domain=None, range=Optional[Union[str, SampleSampleCompositeKey]])

slots.propertyX = Slot(uri=SD.propertyX, name="propertyX", curie=SD.curie('propertyX'),
                   model_uri=SD.propertyX, domain=None, range=Optional[str])

slots.propertyY = Slot(uri=SD.propertyY, name="propertyY", curie=SD.curie('propertyY'),
                   model_uri=SD.propertyY, domain=None, range=Optional[str])

slots.unitXString = Slot(uri=SD.unitXString, name="unitXString", curie=SD.curie('unitXString'),
                   model_uri=SD.unitXString, domain=None, range=Optional[str])

slots.unitYString = Slot(uri=SD.unitYString, name="unitYString", curie=SD.curie('unitYString'),
                   model_uri=SD.unitYString, domain=None, range=Optional[str])

slots.xValuesJSON = Slot(uri=SD.xValuesJSON, name="xValuesJSON", curie=SD.curie('xValuesJSON'),
                   model_uri=SD.xValuesJSON, domain=None, range=Optional[str])

slots.yValuesJSON = Slot(uri=SD.yValuesJSON, name="yValuesJSON", curie=SD.curie('yValuesJSON'),
                   model_uri=SD.yValuesJSON, domain=None, range=Optional[str])

slots.xMin = Slot(uri=SD.xMin, name="xMin", curie=SD.curie('xMin'),
                   model_uri=SD.xMin, domain=None, range=Optional[float])

slots.xMax = Slot(uri=SD.xMax, name="xMax", curie=SD.curie('xMax'),
                   model_uri=SD.xMax, domain=None, range=Optional[float])

slots.yMin = Slot(uri=SD.yMin, name="yMin", curie=SD.curie('yMin'),
                   model_uri=SD.yMin, domain=None, range=Optional[float])

slots.yMax = Slot(uri=SD.yMax, name="yMax", curie=SD.curie('yMax'),
                   model_uri=SD.yMax, domain=None, range=Optional[float])

slots.pointCount = Slot(uri=SD.pointCount, name="pointCount", curie=SD.curie('pointCount'),
                   model_uri=SD.pointCount, domain=None, range=Optional[int])

slots.comments = Slot(uri=SD.comments, name="comments", curie=SD.curie('comments'),
                   model_uri=SD.comments, domain=None, range=Optional[str])

slots.runId = Slot(uri=DCTERMS.identifier, name="runId", curie=DCTERMS.curie('identifier'),
                   model_uri=SD.runId, domain=None, range=str)

slots.atTime = Slot(uri=PROV.atTime, name="atTime", curie=PROV.curie('atTime'),
                   model_uri=SD.atTime, domain=None, range=Union[str, XSDDateTime])

slots.endedAtTime = Slot(uri=PROV.endedAtTime, name="endedAtTime", curie=PROV.curie('endedAtTime'),
                   model_uri=SD.endedAtTime, domain=None, range=Optional[Union[str, XSDDateTime]])

slots.used = Slot(uri=PROV.used, name="used", curie=PROV.curie('used'),
                   model_uri=SD.used, domain=None, range=Union[str, URI])

slots.wasAssociatedWith = Slot(uri=PROV.wasAssociatedWith, name="wasAssociatedWith", curie=PROV.curie('wasAssociatedWith'),
                   model_uri=SD.wasAssociatedWith, domain=None, range=Optional[Union[str, URI]])

slots.wasGeneratedBy = Slot(uri=PROV.wasGeneratedBy, name="wasGeneratedBy", curie=PROV.curie('wasGeneratedBy'),
                   model_uri=SD.wasGeneratedBy, domain=None, range=Optional[Union[str, IngestionActivityRunId]])

slots.personCompositeKey = Slot(uri=DCTERMS.identifier, name="personCompositeKey", curie=DCTERMS.curie('identifier'),
                   model_uri=SD.personCompositeKey, domain=None, range=str)

slots.givenName = Slot(uri=SCHEMAORG.givenName, name="givenName", curie=SCHEMAORG.curie('givenName'),
                   model_uri=SD.givenName, domain=None, range=Optional[str])

slots.familyName = Slot(uri=SCHEMAORG.familyName, name="familyName", curie=SCHEMAORG.curie('familyName'),
                   model_uri=SD.familyName, domain=None, range=Optional[str])

slots.periodicalSlug = Slot(uri=DCTERMS.identifier, name="periodicalSlug", curie=DCTERMS.curie('identifier'),
                   model_uri=SD.periodicalSlug, domain=None, range=str)

slots.periodicalName = Slot(uri=SCHEMAORG.name, name="periodicalName", curie=SCHEMAORG.curie('name'),
                   model_uri=SD.periodicalName, domain=None, range=Optional[str])

slots.alternateName = Slot(uri=SCHEMAORG.alternateName, name="alternateName", curie=SCHEMAORG.curie('alternateName'),
                   model_uri=SD.alternateName, domain=None, range=Optional[str])

slots.Paper_SID = Slot(uri=DCTERMS.identifier, name="Paper_SID", curie=DCTERMS.curie('identifier'),
                   model_uri=SD.Paper_SID, domain=Paper, range=Union[str, PaperSID])

slots.Sample_sampleCompositeKey = Slot(uri=DCTERMS.identifier, name="Sample_sampleCompositeKey", curie=DCTERMS.curie('identifier'),
                   model_uri=SD.Sample_sampleCompositeKey, domain=Sample, range=Union[str, SampleSampleCompositeKey])

slots.Curve_curveCompositeKey = Slot(uri=DCTERMS.identifier, name="Curve_curveCompositeKey", curie=DCTERMS.curie('identifier'),
                   model_uri=SD.Curve_curveCompositeKey, domain=Curve, range=Union[str, CurveCurveCompositeKey])

slots.Descriptor_descriptorCompositeKey = Slot(uri=DCTERMS.identifier, name="Descriptor_descriptorCompositeKey", curie=DCTERMS.curie('identifier'),
                   model_uri=SD.Descriptor_descriptorCompositeKey, domain=Descriptor, range=Union[str, DescriptorDescriptorCompositeKey])

slots.IngestionActivity_runId = Slot(uri=DCTERMS.identifier, name="IngestionActivity_runId", curie=DCTERMS.curie('identifier'),
                   model_uri=SD.IngestionActivity_runId, domain=IngestionActivity, range=Union[str, IngestionActivityRunId])

slots.Person_personCompositeKey = Slot(uri=DCTERMS.identifier, name="Person_personCompositeKey", curie=DCTERMS.curie('identifier'),
                   model_uri=SD.Person_personCompositeKey, domain=Person, range=Union[str, PersonPersonCompositeKey])

slots.Periodical_periodicalSlug = Slot(uri=DCTERMS.identifier, name="Periodical_periodicalSlug", curie=DCTERMS.curie('identifier'),
                   model_uri=SD.Periodical_periodicalSlug, domain=Periodical, range=Union[str, PeriodicalPeriodicalSlug])

