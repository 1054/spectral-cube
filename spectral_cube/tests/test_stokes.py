import numpy as np
from numpy.testing import assert_allclose, assert_equal

from astropy.wcs import WCS
from astropy.tests.helper import pytest
from astropy.utils import OrderedDict, NumpyRNGContext

from ..spectral_cube import SpectralCube, StokesSpectralCube
from ..masks import BooleanArrayMask

class TestStokesSpectralCube():

    def setup_class(self):

        self.wcs = WCS(naxis=3)
        self.wcs.wcs.ctype = ['RA---TAN', 'DEC--TAN', 'FREQ']
        self.data = np.arange(4)[:,None,None,None] * np.ones((5, 20, 30))

    def test_direct_init(self):
        stokes_data = dict(I=SpectralCube(self.data[0], self.wcs),
                           Q=SpectralCube(self.data[1], self.wcs),
                           U=SpectralCube(self.data[2], self.wcs),
                           V=SpectralCube(self.data[3], self.wcs))
        cube = StokesSpectralCube(stokes_data)

    def test_direct_init_invalid_type(self):
        stokes_data = dict(I=self.data[0],
                           Q=self.data[1],
                           U=self.data[2],
                           V=self.data[3])
        with pytest.raises(TypeError) as exc:
            cube = StokesSpectralCube(stokes_data, self.wcs)
        assert exc.value.args[0] == "stokes_data should be a dictionary of SpectralCube objects"

    def test_direct_init_invalid_shape(self):
        stokes_data = dict(I=SpectralCube(np.ones((6, 2, 30)), self.wcs),
                           Q=SpectralCube(self.data[1], self.wcs),
                           U=SpectralCube(self.data[2], self.wcs),
                           V=SpectralCube(self.data[3], self.wcs))
        with pytest.raises(ValueError) as exc:
            cube = StokesSpectralCube(stokes_data, self.wcs)
        assert exc.value.args[0] == "All spectral cubes shoul have the same shape"


    @pytest.mark.parametrize('component', ('I', 'Q', 'U', 'V', 'RR', 'RL', 'LR', 'LL'))
    def test_valid_component_name(self, component):
        stokes_data = {component: SpectralCube(self.data[0], self.wcs)}
        cube = StokesSpectralCube(stokes_data)

    @pytest.mark.parametrize('component', ('A', 'B', 'IQUV'))
    def test_invalid_component_name(self, component):
        stokes_data = {component: SpectralCube(self.data[0], self.wcs)}
        with pytest.raises(ValueError) as exc:
            cube = StokesSpectralCube(stokes_data, self.wcs)
        assert exc.value.args[0] == "Invalid Stokes component: {0} - should be one of I, Q, U, V, RR, LL, RL, LR".format(component)

    def test_invalid_wcs(self):
        wcs2 = WCS(naxis=3)
        wcs2.wcs.ctype = ['GLON-CAR', 'GLAT-CAR', 'FREQ']
        stokes_data = dict(I=SpectralCube(self.data[0], self.wcs),
                           Q=SpectralCube(self.data[1], wcs2))
        with pytest.raises(ValueError) as exc:
            cube = StokesSpectralCube(stokes_data)
        assert exc.value.args[0] == "All spectral cubes in stokes_data should have the same WCS"

    def test_attributes(self):
        stokes_data = dict(I=SpectralCube(self.data[0], self.wcs),
                           Q=SpectralCube(self.data[1], self.wcs),
                           U=SpectralCube(self.data[2], self.wcs),
                           V=SpectralCube(self.data[3], self.wcs))
        cube = StokesSpectralCube(stokes_data)
        assert_allclose(cube.I.unmasked_data[...], 0)
        assert_allclose(cube.Q.unmasked_data[...], 1)
        assert_allclose(cube.U.unmasked_data[...], 2)
        assert_allclose(cube.V.unmasked_data[...], 3)

    def test_dir(self):
        stokes_data = dict(I=SpectralCube(self.data[0], self.wcs),
                           Q=SpectralCube(self.data[1], self.wcs),
                           U=SpectralCube(self.data[2], self.wcs))
        cube = StokesSpectralCube(stokes_data)
        for stokes in 'IQU':
            assert stokes in cube.__dir__()
        assert 'V' not in cube.__dir__()

    def test_mask(self):

        with NumpyRNGContext(12345):
            mask1 = BooleanArrayMask(np.random.random((5, 20, 30)) > 0.2, self.wcs)
            # Deliberately don't use a BooleanArrayMask to check auto-conversion
            mask2 = np.random.random((5, 20, 30)) > 0.4

        stokes_data = dict(I=SpectralCube(self.data[0], self.wcs),
                           Q=SpectralCube(self.data[1], self.wcs),
                           U=SpectralCube(self.data[2], self.wcs),
                           V=SpectralCube(self.data[3], self.wcs))
        cube1 = StokesSpectralCube(stokes_data, mask=mask1)

        cube2 = cube1.with_mask(mask2)
        assert_equal(cube2.mask._include(), (mask1)._include() & mask2)

    def test_mask_invalid_component_name(self):
        stokes_data = {'BANANA': SpectralCube(self.data[0], self.wcs)}
        with pytest.raises(ValueError) as exc:
            cube = StokesSpectralCube(stokes_data)
        assert exc.value.args[0] == "Invalid Stokes component: BANANA - should be one of I, Q, U, V, RR, LL, RL, LR"

    def test_mask_invalid_shape(self):
        stokes_data = dict(I=SpectralCube(self.data[0], self.wcs),
                           Q=SpectralCube(self.data[1], self.wcs),
                           U=SpectralCube(self.data[2], self.wcs),
                           V=SpectralCube(self.data[3], self.wcs))
        mask1 = BooleanArrayMask(np.random.random((5, 20, 15)) > 0.2, self.wcs)
        with pytest.raises(ValueError) as exc:
            cube1 = StokesSpectralCube(stokes_data, mask=mask1)
        assert exc.value.args[0] == "Mask shape is not broadcastable to data shape: (5, 20, 15) vs (5, 20, 30)"

    def test_separate_mask(self):

        with NumpyRNGContext(12345):
            mask1 = BooleanArrayMask(np.random.random((5, 20, 30)) > 0.2, self.wcs)
            mask2 = [BooleanArrayMask(np.random.random((5, 20, 30)) > 0.4, self.wcs) for i in range(4)]
            mask3 = BooleanArrayMask(np.random.random((5, 20, 30)) > 0.2, self.wcs)

        stokes_data = dict(I=SpectralCube(self.data[0], self.wcs, mask=mask2[0]),
                           Q=SpectralCube(self.data[1], self.wcs, mask=mask2[1]),
                           U=SpectralCube(self.data[2], self.wcs, mask=mask2[2]),
                           V=SpectralCube(self.data[3], self.wcs, mask=mask2[3]))

        cube1 = StokesSpectralCube(stokes_data, mask=mask1)

        assert_equal(cube1.I.mask._include(), (mask1 & mask2[0])._include())
        assert_equal(cube1.Q.mask._include(), (mask1 & mask2[1])._include())
        assert_equal(cube1.U.mask._include(), (mask1 & mask2[2])._include())
        assert_equal(cube1.V.mask._include(), (mask1 & mask2[3])._include())

        cube2 = cube1.I.with_mask(mask3)
        assert_equal(cube2.mask._include(), (mask1 & mask2[0] & mask3)._include())
