# VerilogASTBench

This work introduces a semantically enhanced AST-based Verilog parsing.

## Requirement

You can install the required packages for running this project using:

```
pip install -r requirements.txt

```


The project file structure is as below:

```text
├─ ast_analysis and repair/          
│  ├─ custom_ast.py      # Defines custom AST node classes for Verilog code parsing.
│  ├─ AST.py             # Core AST construction
│  ├─ ASTAPI.py          # Provides API functions.
│  └─ Repair.py          # Implements AST-guided Verilog code repair logic.
│
├─ Code cleanup.py       # Cleans dataset.  
│
├─ data_collection/                  
│  ├─ clone_repos.py     # Script to clone GitHub repositories for dataset collection.       
│  └─ find_repos.py      # Searches for relevant repositories containing Verilog code.
│
├─ Fig/                             
│  ├─ Fig.pdf
│  └─ Fig.py
│
└─ validation/                       
   ├─ Complexity analysis.py         # Evaluates structural/semantic complexity of Verilog code.
   ├─ gen_verilog_from_prompts.py    # Generates Verilog from LLM prompts for validation.
   ├─ Similarity calculation.py      # Computes similarity scores between generated and reference code.
   └─ validation_type_dataset.py     # Builds and tests validation datasets with different task types.
├── requirements.txt

```

### The full dataset
```
https://drive.google.com/file/d/10EwzGJ5Ihf0rmGSxDtKdJ84edHstVKpN/view?usp=drive_link

