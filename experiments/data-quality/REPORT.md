# Starrydata RDF data-quality audit

- Endpoint: `http://10.0.0.1:7878/query`
- Total curves: **233,103**

## Summary

- **Impossible** (definite bugs): **2,831** records
- **Suspicious** (review): **7** records

| severity | check | count |
|---|---|---|
| 🔴 impossible | Absurd |yMax| (> 1e25) beyond any physical quantity | 1,666 |
| 🔴 impossible | Negative value for a non-negative quantity | 1,130 |
| 🔴 impossible | ZT peak above the physical ceiling or negative | 21 |
| 🔴 impossible | Temperature x-axis below absolute zero | 14 |
| 🟢 impossible | Inconsistent y aggregates (yMin > yMax) | 0 |
| 🟢 impossible | Inconsistent x aggregates (xMin > xMax) | 0 |
| 🟢 impossible | Curve with pointCount <= 0 | 0 |
| 🟡 suspicious | ZT peak in the record-questionable band (3.0, 3.5] | 7 |
| 🟢 suspicious | Curve without a propertyY label | 0 |
| 🟢 info | Curve without a yMax aggregate | 3,871 |

### Absurd |yMax| (> 1e25) beyond any physical quantity  (impossible, 1,666)

No materials quantity reaches 1e25 (even carrier concentration tops out ~1e23 cm^-3). Values above this are unit/scale or parsing errors. (A lower threshold like 1e6 would false-positive on legitimate carrier concentration ~1e19-1e21.)

| y | py | fig |
|---|---|---|
| 41949000000000000000000000000000000000000000000000000 | Electrical resistivity | 6a |
| 4663188000000000000000000000000000000000000000000 | Carrier concentration | 7(a) |
| 4399738000000000000000000000000000000000000000000 | Carrier concentration | 7(a) |
| 4370593000000000000000000000000000000000000000000 | Carrier concentration | 7(a) |
| 4305722000000000000000000000000000000000000000000 | Carrier concentration | 7(a) |
| 3654666000000000000000000000000000000000000000000 | Carrier concentration | 7(a) |
| 435863900000000000000000000000000000000000000 | Carrier concentration | 6b |
| 10639350000000000000000000000000000 | Carrier concentration | 3 |
| 8794797000000000000000000000000000 | Carrier concentration | 3 |
| 4245293000000000000000000000000000 | Carrier concentration | 5 |
| 403879700000000000000000000000000 | Carrier concentration | 4 |
| 297556000000000000000000000000000 | Carrier concentration | 4 |
| 99379360000000000000000000000000 | Carrier concentration | 4 |
| 91552030000000000000000000000000 | Carrier concentration | 5(b) |
| 80102810000000000000000000000000 | Carrier concentration | 4 |
| 65365600000000000000000000000000 | Carrier concentration | 5(b) |
| 62432790000000000000000000000000 | Carrier concentration | 5(b) |
| 45308520000000000000000000000000 | Carrier concentration | 5(b) |
| 36183870000000000000000000000000 | Carrier concentration | 5 |
| 32779620000000000000000000000000 | Carrier concentration | 5 |
| 9148466000000000000000000000000 | Carrier concentration | 5 |
| 3096771000000000000000000000000 | Carrier concentration | 4b (left) |
| 2710481000000000000000000000000 | Carrier concentration | 4 |
| 876700000000000000000000000000 | Carrier concentration | 4c |
| 494422400000000000000000000000 | Carrier concentration | 4 |
| 399963400000000000000000000000 | Carrier concentration | 4b (left) |
| 338555700000000000000000000000 | Carrier concentration | 3 |
| 295602200000000000000000000000 | Carrier concentration | 5 |
| 239200000000000000000000000000 | Carrier concentration | 8(b) |
| 218567600000000000000000000000 | Carrier concentration | 6 |

_…and 1,636 more._

### Negative value for a non-negative quantity  (impossible, 1,130)

These quantities are physically >= 0: thermal/electrical conductivity, resistivity, carrier concentration, power factor (S^2*sigma). yMin < 0 is a sign/parse error. EXCLUDED to avoid false positives: log()/ln() axes (legitimately negative when value < 1), 'coefficient' (e.g. Temperature Coefficient of Resistivity is legitimately negative), mobility (Hall mobility sign convention), and Seebeck/thermopower/Hall (sign is meaningful).

| lo | py | fig |
|---|---|---|
| -808602700000000000000000000000 | Carrier concentration | 4 |
| -447234200000000000000000000000 | Carrier concentration | 4 |
| -6202036000000000000000000000 | Carrier concentration | 7 |
| -5121179000000000000000000000 | Carrier concentration | 7 |
| -4027897000000000000000000000 | Carrier concentration | 7 |
| -2652237000000000000000000000 | Carrier concentration | 7 |
| -424180000000000000000000000 | Carrier concentration | 7_upper |
| -147250000000000000000000000 | Carrier concentration | 7_upper |
| -66587450000000000000000000 | Carrier concentration | 5(d) |
| -64653060000000000000000000 | Carrier concentration | 3a |
| -52710770000000000000000000 | Carrier concentration | 5(d) |
| -51839580000000000000000000 | Carrier concentration | 5(d) |
| -48305700000000000000000000 | Carrier concentration | S5 |
| -46117600000000000000000000 | Carrier concentration | 5(d) |
| -42340140000000000000000000 | Carrier concentration | 3a |
| -40598640000000000000000000 | Carrier concentration | 3a |
| -40537480000000000000000000 | Carrier concentration | 5c |
| -39332620000000000000000000 | Carrier concentration | 5(d) |
| -39018560000000000000000000 | Carrier concentration | 6(b) |
| -38857140000000000000000000 | Carrier concentration | 3a |
| -38702980000000000000000000 | Carrier concentration | 5(d) |
| -38515620000000000000000000 | Carrier concentration | 4(a) inset |
| -36462590000000000000000000 | Carrier concentration | 3a |
| -15949070000000000000000000 | Carrier concentration | 8(b) |
| -12608700000000000000000000 | Carrier concentration | 8(b) |
| -10586590000000000000000000 | Carrier concentration | 8(a) |
| -8212291000000000000000000 | Carrier concentration | 8(a) |
| -7549892000000000000000000 | Carrier concentration | 4lower |
| -7307692000000000000000000 | Carrier concentration | 8(b) |
| -7101449000000000000000000 | Carrier concentration | 8(b) |

_…and 1,100 more._

### ZT peak above the physical ceiling or negative  (impossible, 21)

ZT >= 0 always, and real peak ZT tops out ~3.1. yMax > 3.5 or < 0 indicates a mislabeled axis or digitization/scale error.

| y | py | comp | title |
|---|---|---|---|
| 13054 | ZT | Bi0.5Sb1.5Te3 | Nanoheterojunction‐Mediated Thermoelectric Strategy for Cancer Surgical Adjuvant |
| 9405.4 | ZT | Bi2Te2.8Se0.2 | Nanoheterojunction‐Mediated Thermoelectric Strategy for Cancer Surgical Adjuvant |
| 245.8 | ZT | Ti0.9Sc0.1CoSb | Synthesis, electrical transport, magnetic properties and electronic structure of |
| 241.6 | ZT | Ti0.93Sc0.07CoSb | Synthesis, electrical transport, magnetic properties and electronic structure of |
| 191.1 | ZT | Ti0.95Sc0.05CoSb | Synthesis, electrical transport, magnetic properties and electronic structure of |
| 143 | ZT | Ti0.85Sc0.15CoSb | Synthesis, electrical transport, magnetic properties and electronic structure of |
| 112.4 | ZT | Ti0.97Sc0.03CoSb | Synthesis, electrical transport, magnetic properties and electronic structure of |
| 21.63 | ZT | Ti0.995Sc0.005CoSb | Synthesis, electrical transport, magnetic properties and electronic structure of |
| 21.03 | ZT | Ti0.99Sc0.01CoSb | Synthesis, electrical transport, magnetic properties and electronic structure of |
| 10.9983 | Figure of merit Z | B4C | The influence of W2B5 addition on microstructure and thermoelectric properties o |
| 4.687116 | ZT | Bi0.5Sb1.5Te3 | Investigation of the Electrophysical and Thermoelectric Properties of Films Fabr |
| 4.531847 | ZT | Ge0.98Si0.02 | Functionally Graded Ge1–xSixThermoelectrics by Simultaneous Band Gap and Carrier |
| 4.401097 | ZT | Bi2Te2.8Se0.2 | Investigation of the Electrophysical and Thermoelectric Properties of Films Fabr |
| 4.313886 | ZT | Bi2Se0.3Te2.7 | Comparison of thermoelectric properties of Bi2Te3 and Bi2Se0·3Te2.7 thin film ma |
| 4.056407 | ZT | Bi0.5Sb1.5Te3 | Investigation of the Electrophysical and Thermoelectric Properties of Films Fabr |
| 3.964 | ZT | (HSC6H4OH)39.14(Sb2Te3)60.86 | An Organic–Inorganic Superlattice with Nanocrystal‐Amorphous Composite Nanolayer |
| 3.547952 | ZT | Yb1.1Zn1.90In0.10Sb2(InSb)0.1 | Spontaneously promoted carrier mobility and strengthened phonon scattering in p- |
| 3.500827 | ZT | Yb1.13Zn1.90In0.10Sb2(InSb)0.13 | Spontaneously promoted carrier mobility and strengthened phonon scattering in p- |
| -0.0000143 | ZT | Pr0.4Sr0.6FeO3 | High-Temperature Thermoelectric Properties of Pr<sub>1−</sub><i><sub>x</sub></i> |
| -0.0005313908 | ZT | Cs7.9Al7.9Si38.1 | A Combined Metal-Halide/Metal Flux Synthetic Route towards Type-I Clathrates: Cr |
| -1784.953 | ZT | Bi2Te3 | Tight-binding modeling of thermoelectric properties of bismuth telluride |

### Temperature x-axis below absolute zero  (impossible, 14)

xMin < -273.16 is below 0 K regardless of whether the axis is K or degC — an impossible temperature.

| lo | px | fig |
|---|---|---|
| -9270 | Inversed Temperature | 4 |
| -5000 | Inversed Temperature | Abb1 |
| -3417 | Inversed Temperature | Fig 2(b) |
| -3412.2 | Temperature | 4 |
| -3412.2 | Temperature | 4 |
| -3404.1 | Temperature | 4 |
| -3342 | Inversed Temperature | Fig 2(b) |
| -3015 | Inversed Temperature | Fig 2(b) |
| -2388 | Inversed Temperature | Fig 2(b) |
| -619.15 | Temperature | 3_L |
| -462.1 | Temperature | 4(e) |
| -411.05 | Temperature | 3_a |
| -309.05 | Temperature | 3-4b |
| -279.05 | Temperature | 4_R |

### ZT peak in the record-questionable band (3.0, 3.5]  (suspicious, 7)

Above the well-established record territory (~3.1). Real, but worth verifying against the source figure before quoting as a 'record'.

| y | comp | title |
|---|---|---|
| 3.415328 | Ge0.95Si0.05 | Functionally Graded Ge1–xSixThermoelectrics by Simultaneous Band Gap and Carrier |
| 3.171771 | (Sb2Se3)0.05(Sb0.75Bi0.25)2Te3 | The effect of Tl2Te3 on the properties of the solid solution (Sb2Te3)0.75.(Bi2Te |
| 3.094071 | Yb1.07Zn1.90In0.10Sb2(InSb)0.07 | Spontaneously promoted carrier mobility and strengthened phonon scattering in p- |
| 3.094 | Sn0.77Pb0.23Se0.95Cl0.05 | Extending the temperature range of the                     <i>Cmcm</i>           |
| 3.083813 | Na0.03Sn0.992Se | Polycrystalline SnSe with a thermoelectric figure of merit greater than the sing |
| 3.027055 | Tl0.01(Sb0.75Bi0.25)2Te3 | The effect of Tl2Te3 on the properties of the solid solution (Sb2Te3)0.75.(Bi2Te |
| 3.011 | (Cu1.99Se)0.9965(AgSbF6)0.0035 | Highly stabilized and efficient thermoelectric copper selenide |

### Curve without a yMax aggregate  (info, 3,871)

No sd:yMax means peak/range queries silently skip the curve. May be legitimate (empty y[]) but worth knowing the volume.

| py | fig |
|---|---|
| Seebeck coefficient | 4 |
| Carrier concentration | Table 1 |
| Electrical resistivity | 4a |
| Specific heat capacity_K^(-2) | 5 |
| Thermal conductivity | 3(d) |
| Voltage | 2b_charge |
| Electrical resistivity | 1 lower |
| Lattice thermal conductivity | 5(b) |
| Seebeck coefficient | 3a_2 |
| Seebeck coefficient | 1B |
| Voltage | 6c_down |
| Thermal conductivity | 6 |
| Carrier mobility | 6(d) |
| Thermal conductivity | 11(a) |
| Power factor | 2 |
| Thermal conductivity | 6b |
| Dielectric permittivity (ε’) | 5c |
| Thermal conductivity | 3a |
| Temperature Coefficient of Resistivity | 7 |
| Electrical resistivity | 3 |

_…and 3,851 more._
