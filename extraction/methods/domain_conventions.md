# BRF Sjötungan — Domain Conventions

Reference for recurring abbreviations and conventions found in annual reports,
kallelser, and stämmoprotokoll.

## Apartment markers ("M-numbers")

Protocols and motions often identify residents by an **M-number** after their
name, e.g. *Marie Hedmark, M66 och Camilla Holmgren, M62*.

The M-number refers to a specific address on Myggdalsvägen:

| Marker | Address |
|--------|---------------------|
| M62 | Myggdalsvägen 68 |
| M66 | Myggdalsvägen 66 |

> The full mapping is not yet documented. Add rows as they are confirmed from
> protocols or other sources.

### Handling in extraction scripts

`extract_motion_protocol_decisions.py` strips trailing apartment markers
(e.g. `M84`, `M. 84`) from title/author strings so they don't interfere with
period-based title–author splitting. The relevant regex:

```python
re.sub(r"\s+M\.?\s*\d{1,3}$", "", text)
```
