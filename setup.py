#!/usr/bin/env python
# Licensed under a 3-clause BSD style license - see LICENSE.rst

import os
from setuptools import setup

# NOTE: The configuration for the package, including the name, version, and
# other information are set in the setup.cfg file. Here we mainly set up
# setup_requires and install_requires since these are determined
# programmatically.

setup(use_scm_version={'write_to': os.path.join('spectral_cube', 'version.py')})
