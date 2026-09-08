# Human scoring — QA notes (Analyze experiment)

For each agent `qa_notes` item:

- `would_file`: the model's claim
- `human_agree`: 1 if you would file this as a tester

A usable QA agent produces notes a human would actually open a ticket for.
Empty notes on a messy StS UI is a miss.
