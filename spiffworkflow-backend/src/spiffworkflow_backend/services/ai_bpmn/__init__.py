"""AI assisted BPMN generation.

The LLM never reads or writes BPMN XML. Diagrams are exchanged with the model as a compact
line based outline and the model replies with compact edit operations. XML parsing, merging
and layout are done deterministically in this package.
"""
