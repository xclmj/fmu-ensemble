# -*- coding: utf-8 -*-
"""Testing fmu-ensemble."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import pandas as pd

from fmu import config
from fmu.ensemble import ScratchEnsemble

fmux = config.etc.Interaction()
logger = fmux.basiclogger(__name__)

if not fmux.testsetup():
    raise SystemExit()


def test_virtualensemble():

    if '__file__' in globals():
        # Easen up copying test code into interactive sessions
        testdir = os.path.dirname(os.path.abspath(__file__))
    else:
        testdir = os.path.abspath('.')

    reekensemble = ScratchEnsemble('reektest',
                                   testdir +
                                   '/data/testensemble-reek001/' +
                                   'realization-*/iter-0')
    reekensemble.from_smry(time_index='yearly', column_keys=['F*'])
    vens = reekensemble.to_virtual()

    # Check that we have data for 5 realizations
    assert len(vens['unsmry-yearly']['REAL'].unique()) == 5
    assert len(vens['parameters.txt']) == 5

    # Test virtrealization retrieval:
    vreal = vens.get_realization(2)
    assert vreal.keys() == vens.keys()

    # Test realization removal:
    vens.remove_realizations(3)
    assert len(vens.parameters['REAL'].unique()) == 4
    vens.remove_realizations(3)  # This will give warning
    assert len(vens.parameters['REAL'].unique()) == 4
    assert len(vens['unsmry-yearly']['REAL'].unique()) == 4

    # Test data removal:
    vens.remove_data('parameters.txt')
    assert 'parameters.txt' not in vens.keys()
    vens.remove_data('bogus') # This should only give warning

    # Test data addition. It should(?) work also for earlier nonexisting
    vens.append('betterdata', pd.DataFrame({'REAL': [0, 1, 2, 3, 4, 5, 6, 80],
                                            'NPV': [1000, 2000, 1500,
                                                    2300, 6000, 3000, 800, 9]}))
    assert 'betterdata' in vens.keys()
    #print(vens.agg('mean')['betterdata']['NPV'])
    assert vens.get_realization(3)['betterdata']['NPV'] == 2300
    print(vens.get_df('betterdata'))
    print(vens.keys())
    print("foo")
    print(vens.get_realization(80).get_df('betterdata'))
    print("BAr")
    assert vens.get_realization(0)['betterdata']['NPV'] == 1000
    assert vens.get_realization(1)['betterdata']['NPV'] == 2000
    assert vens.get_realization(2)['betterdata']['NPV'] == 1500
    assert vens.get_realization(80)['betterdata']['NPV'] == 9
    
