# this contains imports plugins that configure py.test for astropy tests.
# by importing them here in conftest.py they are discoverable by py.test
# no matter how it is invoked within the source tree.
from __future__ import print_function, absolute_import, division

import os
from distutils.version import LooseVersion

import pytest
import numpy as np
from astropy.io import fits
from astropy import wcs
from astropy.version import version as astropy_version

if LooseVersion(astropy_version) < LooseVersion('2.0.3'):
    # Astropy is not compatible with the standalone plugins prior this while
    # astroquery requires them, so we need this workaround. This will mess
    # up the test header, but everything else will work.
    from astropy.tests.pytest_plugins import (PYTEST_HEADER_MODULES,
                                              TESTED_VERSIONS)
elif astropy_version < '3.0':
    # With older versions of Astropy, we actually need to import the pytest
    # plugins themselves in order to make them discoverable by pytest.
    from astropy.tests.pytest_plugins import *
else:
    # As of Astropy 3.0, the pytest plugins provided by Astropy are
    # automatically made available when Astropy is installed. This means it's
    # not necessary to import them here, but we still need to import global
    # variables that are used for configuration.
    from astropy.tests.plugins.display import PYTEST_HEADER_MODULES, TESTED_VERSIONS

from astropy.tests.helper import enable_deprecations_as_exceptions

## Uncomment the following line to treat all DeprecationWarnings as
## exceptions
# enable_deprecations_as_exceptions()

# Uncomment and customize the following lines to add/remove entries
# from the list of packages for which version numbers are displayed
# when running the tests
PYTEST_HEADER_MODULES['Astropy'] = 'astropy'
PYTEST_HEADER_MODULES['regions'] = 'regions'
PYTEST_HEADER_MODULES['APLpy'] = 'aplpy'

# This is to figure out the spectral-cube's version, rather than astropy's
#from .version import version, astropy_helpers_version


#packagename = os.path.basename(os.path.dirname(__file__))
#TESTED_VERSIONS[packagename] = version
#TESTED_VERSIONS['astropy_helpers'] = astropy_helpers_version


# Import casatools and casatasks here if available as they can otherwise
# cause a segfault if imported later on during tests.
try:
    import casatools
    import casatasks
except ImportError:
    pass

HEADER_FILENAME = os.path.join(os.path.dirname(__file__), 'tests',
                               'data', 'header_jybeam.hdr')


def transpose(d, h, axes):
    d = d.transpose(np.argsort(axes))
    h2 = h.copy()

    for i in range(len(axes)):
        for key in ['NAXIS', 'CDELT', 'CRPIX', 'CRVAL', 'CTYPE', 'CUNIT']:
            h2['%s%i' % (key, i + 1)] = h['%s%i' % (key, axes[i] + 1)]

    return d, h2


def prepare_4_beams():
    beams = np.recarray(4, dtype=[('BMAJ', '>f4'), ('BMIN', '>f4'),
                                  ('BPA', '>f4'), ('CHAN', '>i4'),
                                  ('POL', '>i4')])
    beams['BMAJ'] = [0.4,0.3,0.3,0.4] # arcseconds
    beams['BMIN'] = [0.1,0.2,0.2,0.1]
    beams['BPA'] = [0,45,60,30] # degrees
    beams['CHAN'] = [0,1,2,3]
    beams['POL'] = [0,0,0,0]
    beams = fits.BinTableHDU(beams)
    return beams


def prepare_advs_data():
    # Single Stokes
    h = fits.header.Header.fromtextfile(HEADER_FILENAME)
    h['BUNIT'] = 'K' # Kelvins are a valid unit, JY/BEAM are not: they should be tested separately
    h['NAXIS1'] = 2
    h['NAXIS2'] = 3
    h['NAXIS3'] = 4
    h['NAXIS4'] = 1
    np.random.seed(42)
    d = np.random.random((1, 2, 3, 4))
    return d, h


@pytest.fixture
def data_advs(tmp_path):
    d, h = prepare_advs_data()
    fits.writeto(tmp_path / 'advs.fits', d, h)
    return tmp_path / 'advs.fits'


@pytest.fixture
def data_dvsa(tmp_path):
    d, h = prepare_advs_data()
    d, h = transpose(d, h, [1, 2, 3, 0])
    fits.writeto(tmp_path / 'dvsa.fits', d, h)
    return tmp_path / 'dvsa.fits'

@pytest.fixture
def data_vsad(tmp_path):
    d, h = prepare_advs_data()
    d, h = transpose(d, h, [1, 2, 3, 0])
    d, h = transpose(d, h, [1, 2, 3, 0])
    fits.writeto(tmp_path / 'vsad.fits', d, h)
    return tmp_path / 'vsad.fits'

@pytest.fixture
def data_sadv(tmp_path):
    d, h = prepare_advs_data()
    d, h = transpose(d, h, [1, 2, 3, 0])
    d, h = transpose(d, h, [1, 2, 3, 0])
    d, h = transpose(d, h, [1, 2, 3, 0])
    fits.writeto(tmp_path / 'sadv.fits', d, h)
    return tmp_path / 'sadv.fits'


@pytest.fixture
def data_sdav(tmp_path):
    d, h = prepare_advs_data()
    d, h = transpose(d, h, [1, 2, 3, 0])
    d, h = transpose(d, h, [1, 2, 3, 0])
    d, h = transpose(d, h, [1, 2, 3, 0])
    d, h = transpose(d, h, [0, 2, 1, 3])
    fits.writeto(tmp_path / 'sdav.fits', d, h)
    return tmp_path / 'sdav.fits'


@pytest.fixture
def data_sdav_beams(tmp_path):
    d, h = prepare_advs_data()
    d, h = transpose(d, h, [1, 2, 3, 0])
    d, h = transpose(d, h, [1, 2, 3, 0])
    d, h = transpose(d, h, [1, 2, 3, 0])
    d, h = transpose(d, h, [0, 2, 1, 3])
    del h['BMAJ'], h['BMIN'], h['BPA']
    # want 4 spectral channels
    np.random.seed(42)
    d = np.random.random((4, 3, 2, 1))
    beams = prepare_4_beams()
    hdul = fits.HDUList([fits.PrimaryHDU(data=d, header=h),
                         beams])
    hdul.writeto(tmp_path / 'sdav_beams.fits')
    return tmp_path / 'sdav_beams.fits'


@pytest.fixture
def data_advs_nobeam(tmp_path):
    d, h = prepare_advs_data()
    del h['BMAJ']
    del h['BMIN']
    del h['BPA']
    fits.writeto(tmp_path / 'advs_nobeam.fits', d, h)
    return tmp_path / 'advs_nobeam.fits'


def prepare_adv_data():
    h = fits.header.Header.fromtextfile(HEADER_FILENAME)
    h['BUNIT'] = 'K' # Kelvins are a valid unit, JY/BEAM are not: they should be tested separately
    h['NAXIS1'] = 2
    h['NAXIS2'] = 3
    h['NAXIS3'] = 4
    h['NAXIS'] = 3
    for k in list(h.keys()):
        if k.endswith('4'):
            del h[k]
    np.random.seed(96)
    d = np.random.random((4, 3, 2))
    return d, h


@pytest.fixture
def data_adv(tmp_path):
    d, h = prepare_adv_data()
    fits.writeto(tmp_path / 'adv.fits', d, h)
    return tmp_path / 'adv.fits'


@pytest.fixture
def data_adv_jybeam_upper(tmp_path):
    d, h = prepare_adv_data()
    h['BUNIT'] = 'JY/BEAM'
    fits.writeto(tmp_path / 'adv_JYBEAM_upper.fits', d, h)
    return tmp_path / 'adv_JYBEAM_upper.fits'


@pytest.fixture
def data_adv_jybeam_lower(tmp_path):
    d, h = prepare_adv_data()
    h['BUNIT'] = 'Jy/beam'
    fits.writeto(tmp_path / 'adv_Jybeam_lower.fits', d, h)
    return tmp_path / 'adv_Jybeam_lower.fits'


@pytest.fixture
def data_adv_jybeam_whitespace(tmp_path):
    d, h = prepare_adv_data()
    h['BUNIT'] = ' Jy / beam '
    fits.writeto(tmp_path / 'adv_Jybeam_whitespace.fits', d, h)
    return tmp_path / 'adv_Jybeam_whitespace.fits'


@pytest.fixture
def data_adv_beams(tmp_path):
    d, h = prepare_adv_data()
    bmaj, bmin, bpa = h['BMAJ'], h['BMIN'], h['BPA']
    del h['BMAJ'], h['BMIN'], h['BPA']
    beams = prepare_4_beams()
    hdul = fits.HDUList([fits.PrimaryHDU(data=d, header=h),
                         beams])
    hdul.writeto(tmp_path / 'adv_beams.fits')
    return tmp_path / 'adv_beams.fits'


@pytest.fixture
def data_vad(tmp_path):
    d, h = prepare_adv_data()
    d, h = transpose(d, h, [2, 0, 1])
    fits.writeto(tmp_path / 'vad.fits', d, h)
    return tmp_path / 'vad.fits'

@pytest.fixture
def data_vda(tmp_path):
    d, h = prepare_adv_data()
    d, h = transpose(d, h, [2, 0, 1])
    d, h = transpose(d, h, [2, 1, 0])
    fits.writeto(tmp_path / 'vda.fits', d, h)
    return tmp_path / 'vda.fits'


@pytest.fixture
def data_vda_jybeam_upper(tmp_path):
    d, h = prepare_adv_data()
    d, h = transpose(d, h, [2, 0, 1])
    d, h = transpose(d, h, [2, 1, 0])
    h['BUNIT'] = 'JY/BEAM'
    fits.writeto(tmp_path / 'vda_JYBEAM_upper.fits', d, h)
    return tmp_path / 'vda_JYBEAM_upper.fits'


@pytest.fixture
def data_vda_jybeam_lower(tmp_path):
    d, h = prepare_adv_data()
    d, h = transpose(d, h, [2, 0, 1])
    d, h = transpose(d, h, [2, 1, 0])
    h['BUNIT'] = 'Jy/beam'
    fits.writeto(tmp_path / 'vda_Jybeam_lower.fits', d, h)
    return tmp_path / 'vda_Jybeam_lower.fits'


@pytest.fixture
def data_vda_jybeam_whitespace(tmp_path):
    d, h = prepare_adv_data()
    d, h = transpose(d, h, [2, 0, 1])
    d, h = transpose(d, h, [2, 1, 0])
    h['BUNIT'] = ' Jy / beam '
    fits.writeto(tmp_path / 'vda_Jybeam_whitespace.fits', d, h)
    return tmp_path / 'vda_Jybeam_whitespace.fits'


@pytest.fixture
def data_vda_beams(tmp_path):
    d, h = prepare_adv_data()
    d, h = transpose(d, h, [2, 0, 1])
    d, h = transpose(d, h, [2, 1, 0])
    h['BUNIT'] = ' Jy / beam '
    del h['BMAJ'], h['BMIN'], h['BPA']
    beams = prepare_4_beams()
    hdul = fits.HDUList([fits.PrimaryHDU(data=d, header=h),
                         beams])
    hdul.writeto(tmp_path / 'vda_beams.fits')
    return tmp_path / 'vda_beams.fits'


def prepare_255_header():
    # make a version with spatial pixels
    h = fits.header.Header.fromtextfile(HEADER_FILENAME)
    for k in list(h.keys()):
        if k.endswith('4'):
            del h[k]
    h['BUNIT'] = 'K' # Kelvins are a valid unit, JY/BEAM are not: they should be tested separately
    return h


@pytest.fixture
def data_255(tmp_path):
    h = prepare_255_header()
    d = np.arange(2*5*5).reshape((2,5,5))
    fits.writeto(tmp_path / '255.fits', d, h)
    return tmp_path / '255.fits'


@pytest.fixture
def data_255_delta(tmp_path):
    h = prepare_255_header()
    # test cube for convolution, regridding
    d = np.zeros([2,5,5], dtype='float')
    d[0,2,2] = 1.0
    fits.writeto(tmp_path / '255_delta.fits', d, h)
    return tmp_path / '255_delta.fits'


@pytest.fixture
def data_455_delta_beams(tmp_path):
    h = prepare_255_header()
    # test cube for convolution, regridding
    d = np.zeros([4,5,5], dtype='float')
    d[:,2,2] = 1.0
    beams = prepare_4_beams()
    hdul = fits.HDUList([fits.PrimaryHDU(data=d, header=h),
                         beams])
    hdul.writeto(tmp_path / '455_delta_beams.fits')
    return tmp_path / '455_delta_beams.fits'


@pytest.fixture
def data_522_delta(tmp_path):
    h = prepare_255_header()
    d = np.zeros([5,2,2], dtype='float')
    d[2,:,:] = 1.0
    fits.writeto(tmp_path / '522_delta.fits', d, h)
    return tmp_path / '522_delta.fits'


def prepare_5_beams():
    beams = np.recarray(5, dtype=[('BMAJ', '>f4'), ('BMIN', '>f4'),
                                ('BPA', '>f4'), ('CHAN', '>i4'),
                                ('POL', '>i4')])
    beams['BMAJ'] = [0.5,0.4,0.3,0.4,0.5] # arcseconds
    beams['BMIN'] = [0.1,0.2,0.3,0.2,0.1]
    beams['BPA'] = [0,45,60,30,0] # degrees
    beams['CHAN'] = [0,1,2,3,4]
    beams['POL'] = [0,0,0,0,0]
    beams = fits.BinTableHDU(beams)
    return beams


@pytest.fixture
def data_522_delta_beams(tmp_path):
    h = prepare_255_header()
    d = np.zeros([5,2,2], dtype='float')
    d[2,:,:] = 1.0
    beams = prepare_5_beams()
    hdul = fits.HDUList([fits.PrimaryHDU(data=d, header=h),
                         beams])
    hdul.writeto(tmp_path / '522_delta_beams.fits')
    return tmp_path / '522_delta_beams.fits'


def prepare_55_header():
    h = fits.header.Header.fromtextfile(HEADER_FILENAME)
    for k in list(h.keys()):
        if k.endswith('4') or k.endswith('3'):
            del h[k]
    h['BUNIT'] = 'K'
    return h


@pytest.fixture
def data_55(tmp_path):
    # Make a 2D spatial version
    h = prepare_55_header()
    d = np.arange(5 * 5).reshape((5, 5))
    fits.writeto(tmp_path / '55.fits', d, h)
    return tmp_path / '55.fits'


@pytest.fixture
def data_55_delta(tmp_path):
    # test cube for convolution, regridding
    h = prepare_55_header()
    d = np.zeros([5, 5], dtype='float')
    d[2, 2] = 1.0
    fits.writeto(tmp_path / '55_delta.fits', d, h)
    return tmp_path / '55_delta.fits'


def prepare_5_header():
    h = wcs.WCS(fits.Header.fromtextfile(HEADER_FILENAME)).sub([wcs.WCSSUB_SPECTRAL]).to_header()
    return h


@pytest.fixture
def data_5_spectral(tmp_path):
    # oneD spectra
    h = prepare_5_header()
    d = np.arange(5, dtype='float')
    fits.writeto(tmp_path / '5_spectral.fits', d, h)
    return tmp_path / '5_spectral.fits'

@pytest.fixture
def data_5_spectral_beams(tmp_path):
    h = prepare_5_header()
    d = np.arange(5, dtype='float')
    beams = prepare_5_beams()
    hdul = fits.HDUList([fits.PrimaryHDU(data=d, header=h),
                         beams])
    hdul.writeto(tmp_path / '5_spectral_beams.fits')
    return tmp_path / '5_spectral_beams.fits'
