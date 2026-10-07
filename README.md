# CSV Reader

A small, dependency-free CSV parser and CLI written in Python.

## Features

- Handles quoted fields, embedded commas, and newlines
- Supports LF, CRLF, and CR line endings
- Streams large files in chunks
- Optional type inference for ints, floats, and booleans
- Outputs as a table or JSON

## Usage

```bash
python minicsv.py sample.csv
python minicsv.py sample.csv --json --infer-types
python minicsv.py sample.csv --no-header