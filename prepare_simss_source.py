"""Reproduce PDS mesh fixes from a checkout of upstream SIMsalabim v5.36.

Usage: python prepare_simss_source.py path/to/SIMsalabim-5.36
The exact resulting sources are distributed in vendor/simsalabim/source.
"""
from pathlib import Path
import shutil
import sys


def prepare(upstream):
    upstream = Path(upstream)
    target = Path(__file__).resolve().parent/'vendor'/'simsalabim'/'source'
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(upstream/'Units', target/'Units', dirs_exist_ok=True)
    (target/'SimSS').mkdir(exist_ok=True)
    shutil.copy2(upstream/'SimSS'/'simss.pas', target/'SimSS'/'simss.pas')
    path = target/'Units'/'DDRoutines.pas'
    text = path.read_text(encoding='utf-8-sig')
    start = text.index('PROCEDURE Make_Grid(VAR stv', text.index('IMPLEMENTATION'))
    end = text.index('PROCEDURE Define_Layers', start)
    block = text[start:end]
    block = block.replace('istart, ifinish : INTEGER;', 'istart, ifinish, GridBudget : INTEGER;')
    block = block.replace('WITH stv DO BEGIN',
                          '{PDS modification 2026-10-14: reserve one extra mesh interval\n'
                          '\t per internal layer boundary before distributing the grid.}\n'
                          '\tGridBudget:=par.NP-(stv.NLayers-1);\n\tWITH stv DO BEGIN', 1)
    block = block.replace('par.NP*par.lyr[i].L/Ltot', 'GridBudget*par.lyr[i].L/Ltot')
    block = block.replace('(par.NP-NumThinLayers*MinGridPointsPerLayer)',
                          '(GridBudget-NumThinLayers*MinGridPointsPerLayer)')
    text = text[:start]+block+text[end:]
    path.write_text(text, encoding='utf-8')
    path = target/'Units'/'TypesAndConstants.pp'
    text = path.read_text(encoding='utf-8-sig')
    text = text.replace('Max_NP\t = 1000;', 'Max_NP\t = 2000; {PDS: support the UI grid limit}')
    if 'Max_NP\t = 2000;' not in text:
        raise RuntimeError('Upstream Max_NP definition does not match 5.36')
    path.write_text(text, encoding='utf-8')
    return target


if __name__ == '__main__':
    print(prepare(sys.argv[1]))
