"""
A class to represent a 3-d position-position-velocity spectral cube.
"""

from __future__ import print_function, absolute_import, division

import uuid
import inspect
import warnings
from functools import wraps

from astropy import units as u
from astropy.io.fits import PrimaryHDU, HDUList
from astropy.wcs.utils import proj_plane_pixel_area

import numpy as np
import dask.array as da

from astropy import stats
from astropy import convolution
from astropy import wcs

from . import wcs_utils
from .spectral_cube import SpectralCube, VaryingResolutionSpectralCube, SIGMA2FWHM, np2wcs
from .utils import cached, warn_slow, VarianceWarning, SliceWarning, BeamWarning, SmoothingWarning
from .lower_dimensional_structures import Projection
from .masks import BooleanArrayMask, is_broadcastable_and_smaller
from .np_compat import allbadtonan

__all__ = ['DaskSpectralCube', 'DaskVaryingResolutionSpectralCube']

try:
    from scipy import ndimage
    SCIPY_INSTALLED = True
except ImportError:
    SCIPY_INSTALLED = False


def nansum_allbadtonan(dask_array, axis=None, keepdims=None):
    return da.reduction(dask_array,
                        allbadtonan(np.nansum),
                        allbadtonan(np.nansum),
                        axis=axis,
                        dtype=dask_array.dtype)


def projection_if_needed(function):

    # check if function defines default projection kwargs
    parameters = inspect.signature(function).parameters

    if 'projection' in parameters:
        default_projection = parameters['projection'].default
    else:
        default_projection = True

    if 'unit' in parameters:
        default_unit = parameters['unit'].default
    else:
        default_unit = 'self'

    @wraps(function)
    def wrapper(self, *args, **kwargs):

        projection = kwargs.get('projection', default_projection)
        unit = kwargs.get('unit', default_unit)

        if unit == 'self':
            unit = self.unit

        out = function(self, *args, **kwargs)

        axis = kwargs.get('axis')

        if isinstance(out, da.Array):
            out = self._compute(out)

        if axis is None:

            # return is scalar
            if unit is not None:
                return u.Quantity(out, unit=unit)
            else:
                return out

        elif projection and axis is not None and self._naxes_dropped(axis) in (1, 2):

            meta = {'collapse_axis': axis}
            meta.update(self._meta)

            if hasattr(axis, '__len__') and len(axis) == 2:
                # if operation is over two spatial dims
                if set(axis) == set((1, 2)):
                    new_wcs = self._wcs.sub([wcs.WCSSUB_SPECTRAL])
                    header = self._nowcs_header
                    # check whether the cube has beams at all
                    # (note that "hasattr(self, 'beam') on an object with no
                    # _beam will result in an exception....?!?!?!?)
                    if hasattr(self, '_beam') and self._beam is not None:
                        bmarg = {'beam': self.beam}
                    elif hasattr(self, '_beams') and self._beams is not None:
                        bmarg = {'beams': self.unmasked_beams}
                    else:
                        bmarg = {}
                    return self._oned_spectrum(value=out,
                                               wcs=new_wcs,
                                               copy=False,
                                               unit=unit,
                                               header=header,
                                               meta=meta,
                                               spectral_unit=self._spectral_unit,
                                               **bmarg
                                              )
                else:
                    warnings.warn("Averaging over a spatial and a spectral "
                                  "dimension cannot produce a Projection "
                                  "quantity (no units or WCS are preserved).",
                                  SliceWarning)
                    return out

            else:
                new_wcs = wcs_utils.drop_axis(self._wcs, np2wcs[axis])
                header = self._nowcs_header

                return Projection(out, copy=False, wcs=new_wcs,
                                  meta=meta,  unit=unit,
                                  header=header)

        else:

            return out

    return wrapper


def ignore_deprecated_kwargs(function):

    @wraps(function)
    def wrapper(self, *args, **kwargs):

        if 'how' in kwargs:
            kwargs.pop('how')
            warnings.warn('The how= argument is ignored by methods of {0} '
                          'since how the operations are carried out is '
                          'controlled by the chunking of the underlying dask '
                          'array.'.format(self.__class__.__name__))

        if 'iterate_rays' in kwargs:
            kwargs.pop('iterate_rays')
            warnings.warn('The iterate_rays= argument is ignored by methods of {0} '
                          'since how the operations are carried out is '
                          'controlled by the chunking of the underlying dask '
                          'array.'.format(self.__class__.__name__))

        if 'progressbar' in kwargs:
            kwargs.pop('progressbar')
            warnings.warn('The progressbar= argument is ignored by methods of {0} '
                          'since you can instead use dask\'s mechanism to '
                          'create one.'.format(self.__class__.__name__))

        if 'update_function' in kwargs:
            kwargs.pop('update_function')
            warnings.warn('The update_function= argument is ignored by methods of {0} '
                          'since you can instead use dask\'s mechanism to '
                          'create one.'.format(self.__class__.__name__))

        return function(self, *args, **kwargs)

    return wrapper


class FilledArrayHandler:
    """
    """

    def __init__(self, cube, fill=np.nan):
        self._cube = cube
        self._fill = fill
        self.shape = cube._data.shape
        self.dtype = cube._data.dtype
        self.ndim = len(self.shape)

    def __getitem__(self, view):
        if self._cube._data[view].size == 0:
            return 0.
        result = self._cube._mask._filled(data=self._cube._data,
                                          view=view,
                                          wcs=self._cube._wcs,
                                          fill=self._fill,
                                          wcs_tolerance=self._cube._wcs_tolerance)
        if isinstance(result, da.Array):
            if result.shape == (0, 0, 0):
                return 0.
            result = result.compute()

        return result


class MaskHandler:
    """
    """

    def __init__(self, cube):
        self._cube = cube
        self._mask = cube.mask
        self.shape = cube._data.shape
        self.dtype = cube._data.dtype
        self.ndim = len(self.shape)

    def __getitem__(self, view):
        if self._cube._data[view].size == 0:
            return False
        else:
            result = self._mask.include(view=view)
            if isinstance(result, da.Array):
                result = result.compute()
        return result


class DaskSpectralCubeMixin:

    def _compute(self, array):
        # For now, always default to serial mode, but we could then expand this
        # to allow different modes.
        return array.compute(scheduler='synchronous')

    def _get_filled_data(self, view=(), fill=np.nan, check_endian=None, use_memmap=None):

        if check_endian:
            if not self._data.dtype.isnative:
                kind = str(self._data.dtype.kind)
                sz = str(self._data.dtype.itemsize)
                dt = '=' + kind + sz
                data = self._data.astype(dt)
            else:
                data = self._data
        else:
            data = self._data

        if self._mask is None:
            return data[view]
        else:
            return da.from_array(FilledArrayHandler(self, fill=fill), name='FilledArrayHandler ' + str(uuid.uuid4()), chunks=data.chunksize)[view]

    def get_mask_array(self):
        """
        Convert the mask to a boolean numpy array
        """
        result = self._mask.include(data=self._data, wcs=self._wcs,
                                    wcs_tolerance=self._wcs_tolerance)
        if isinstance(result, da.Array):
            result = result.compute()
        return result

    @projection_if_needed
    def apply_function(self, function, axis=None, weights=None, unit=None,
                       projection=False, progressbar=False,
                       update_function=None, keep_shape=False, **kwargs):
        """
        Apply a function to valid data along the specified axis or to the whole
        cube, optionally using a weight array that is the same shape (or at
        least can be sliced in the same way)

        Parameters
        ----------
        function : function
            A function that can be applied to a numpy array.  Does not need to
            be nan-aware
        axis : 1, 2, 3, or None
            The axis to operate along.  If None, the return is scalar.
        weights : (optional) np.ndarray
            An array with the same shape (or slicing abilities/results) as the
            data cube
        unit : (optional) `~astropy.units.Unit`
            The unit of the output projection or value.  Not all functions
            should return quantities with units.
        projection : bool
            Return a projection if the resulting array is 2D?
        keep_shape : bool
            If `True`, the returned object will be the same dimensionality as
            the cube.

        Returns
        -------
        result : :class:`~spectral_cube.lower_dimensional_structures.Projection` or `~astropy.units.Quantity` or float
            The result depends on the value of ``axis``, ``projection``, and
            ``unit``.  If ``axis`` is None, the return will be a scalar with or
            without units.  If axis is an integer, the return will be a
            :class:`~spectral_cube.lower_dimensional_structures.Projection` if ``projection`` is set
        """

        if axis is None:
            out = function(self.flattened(), **kwargs)
            if unit is not None:
                return u.Quantity(out, unit=unit)
            else:
                return out

        data = self._get_filled_data(fill=self._fill_value)

        if keep_shape:
            newdata = da.apply_along_axis(function, axis, data, shape=(self.shape[axis],))
        else:
            newdata = da.apply_along_axis(function, axis, data)

        return newdata

    @projection_if_needed
    def apply_numpy_function(self, function, fill=np.nan,
                             reduce=True, how='auto',
                             projection=False,
                             unit=None,
                             check_endian=False,
                             progressbar=False,
                             includemask=False,
                             **kwargs):
        """
        Apply a numpy function to the cube

        Parameters
        ----------
        function : Numpy ufunc
            A numpy ufunc to apply to the cube
        fill : float
            The fill value to use on the data
        reduce : bool
            reduce indicates whether this is a reduce-like operation,
            that can be accumulated one slice at a time.
            sum/max/min are like this. argmax/argmin/stddev are not
        how : cube | slice | ray | auto
           How to compute the moment. All strategies give the same
           result, but certain strategies are more efficient depending
           on data size and layout. Cube/slice/ray iterate over
           decreasing subsets of the data, to conserve memory.
           Default='auto'
        projection : bool
            Return a :class:`~spectral_cube.lower_dimensional_structures.Projection` if the resulting array is 2D or a
            OneDProjection if the resulting array is 1D and the sum is over both
            spatial axes?
        unit : None or `astropy.units.Unit`
            The unit to include for the output array.  For example,
            `SpectralCube.max` calls
            ``SpectralCube.apply_numpy_function(np.max, unit=self.unit)``,
            inheriting the unit from the original cube.
            However, for other numpy functions, e.g. `numpy.argmax`, the return
            is an index and therefore unitless.
        check_endian : bool
            A flag to check the endianness of the data before applying the
            function.  This is only needed for optimized functions, e.g. those
            in the `bottleneck <https://pypi.python.org/pypi/Bottleneck>`_ package.
        progressbar : bool
            Show a progressbar while iterating over the slices through the
            cube?
        kwargs : dict
            Passed to the numpy function.

        Returns
        -------
        result : :class:`~spectral_cube.lower_dimensional_structures.Projection` or `~astropy.units.Quantity` or float
            The result depends on the value of ``axis``, ``projection``, and
            ``unit``.  If ``axis`` is None, the return will be a scalar with or
            without units.  If axis is an integer, the return will be a
            :class:`~spectral_cube.lower_dimensional_structures.Projection` if ``projection`` is set
        """

        # TODO: add support for includemask

        data = self._get_filled_data(fill=fill, check_endian=check_endian)

        # Numpy ufuncs know how to deal with dask arrays
        if function.__module__.startswith('numpy'):
            return function(data, **kwargs)
        else:
            # TODO: implement support for bottleneck? or arbitrary ufuncs?
            raise NotImplementedError()

    def apply_function_parallel_spatial(self,
                                        function,
                                        num_cores=None,
                                        verbose=0,
                                        use_memmap=True,
                                        parallel=True,
                                        **kwargs
                                       ):
        """
        Apply a function in parallel along the spatial dimension.  The
        function will be performed on data with masked values replaced with the
        cube's fill value.

        Parameters
        ----------
        function : function
            The function to apply in the spatial dimension.  It must take
            two arguments: an array representing an image and a boolean array
            representing the mask.  It may also accept ``**kwargs``.  The
            function must return an object with the same shape as the input
            image.
        num_cores : int or None
            The number of cores to use if running in parallel
        verbose : int
            Verbosity level to pass to joblib
        use_memmap : bool
            If specified, a memory mapped temporary file on disk will be
            written to rather than storing the intermediate spectra in memory.
        parallel : bool
            If set to ``False``, will force the use of a single core without
            using ``joblib``.
        kwargs : dict
            Passed to ``function``
        """

        def wrapper(data_slices, **kwargs):
            # Dask doesn't drop the spectral dimension so this function is a
            # wrapper to do that automatically.
            if data_slices.size > 0:
                out = np.zeros_like(data_slices)
                for index in range(data_slices.shape[0]):
                    out[index] = function(data_slices[index], **kwargs)
                return out
            else:
                return data_slices

        # Rechunk so that there is only one chunk in the image plane
        return self._map_blocks_to_cube(wrapper,
                                        rechunk=('auto', -1, -1), fill=self._fill_value, **kwargs)

    def apply_function_parallel_spectral(self,
                                         function,
                                         num_cores=None,
                                         verbose=0,
                                         use_memmap=True,
                                         parallel=True,
                                         **kwargs
                                        ):
        """
        Apply a function in parallel along the spectral dimension.  The
        function will be performed on data with masked values replaced with the
        cube's fill value.

        Parameters
        ----------
        function : function
            The function to apply in the spectral dimension.  It must take
            two arguments: an array representing a spectrum and a boolean array
            representing the mask.  It may also accept ``**kwargs``.  The
            function must return an object with the same shape as the input
            spectrum.
        num_cores : int or None
            The number of cores to use if running in parallel
        verbose : int
            Verbosity level to pass to joblib
        use_memmap : bool
            If specified, a memory mapped temporary file on disk will be
            written to rather than storing the intermediate spectra in memory.
        parallel : bool
            If set to ``False``, will force the use of a single core without
            using ``joblib``.
        kwargs : dict
            Passed to ``function``
        """

        data = self._get_filled_data(fill=self._fill_value)

        # apply_along_axis returns an array with a single chunk, but we need to
        # rechunk here to avoid issues when writing out the data even if it
        # results in a poorer performance.
        data = data.rechunk((-1, 50, 50))

        newdata = da.apply_along_axis(function, 0, data, shape=(self.shape[0],))

        return self._new_cube_with(data=newdata,
                                   wcs=self.wcs,
                                   mask=self.mask,
                                   meta=self.meta,
                                   fill_value=self.fill_value)

    @warn_slow
    @projection_if_needed
    @ignore_deprecated_kwargs
    def sum(self, axis=None, **kwargs):
        """
        Return the sum of the cube, optionally over an axis.
        """
        return self._compute(nansum_allbadtonan(self._get_filled_data(fill=np.nan), axis=axis, **kwargs))

    @warn_slow
    @projection_if_needed
    @ignore_deprecated_kwargs
    def mean(self, axis=None, **kwargs):
        """
        Return the mean of the cube, optionally over an axis.
        """
        return self._compute(da.nanmean(self._get_filled_data(fill=np.nan), axis=axis, **kwargs))

    @warn_slow
    @projection_if_needed
    @ignore_deprecated_kwargs
    def median(self, axis=None, **kwargs):
        """
        Return the median of the cube, optionally over an axis.
        """
        data = self._get_filled_data(fill=np.nan)

        if axis is None:
            return np.nanmedian(self._compute(data), **kwargs)
        else:
            return self._compute(da.nanmedian(self._get_filled_data(fill=np.nan), axis=axis, **kwargs))

    @warn_slow
    @projection_if_needed
    @ignore_deprecated_kwargs
    def percentile(self, q, axis=None, **kwargs):
        """
        Return percentiles of the data.

        Parameters
        ----------
        q : float
            The percentile to compute
        axis : int, or None
            Which axis to compute percentiles over
        """

        data = self._get_filled_data(fill=np.nan)

        if axis is None:
            return np.nanpercentile(data, q, **kwargs)
        else:
            # Rechunk so that there is only one chunk along the desired axis
            data = data.rechunk([-1 if i == axis else 'auto' for i in range(3)])
            return self._compute(data.map_blocks(np.nanpercentile, q=q, drop_axis=axis, axis=axis, **kwargs))

        return self._compute(da.nanpercentile(self._get_filled_data(fill=np.nan), q, axis=axis))

    @warn_slow
    @projection_if_needed
    @ignore_deprecated_kwargs
    def std(self, axis=None, ddof=0, **kwargs):
        """
        Return the mean of the cube, optionally over an axis.

        Other Parameters
        ----------------
        ddof : int
            Means Delta Degrees of Freedom.  The divisor used in calculations
            is ``N - ddof``, where ``N`` represents the number of elements.  By
            default ``ddof`` is zero.
        """
        return self._compute(da.nanstd(self._get_filled_data(fill=np.nan), axis=axis, ddof=ddof, **kwargs))

    @warn_slow
    @projection_if_needed
    @ignore_deprecated_kwargs
    def mad_std(self, axis=None, **kwargs):
        """
        Use astropy's mad_std to compute the standard deviation
        """

        data = self._get_filled_data(fill=np.nan)

        if axis is None:
            # In this case we have to load the full data - even dask's
            # nanmedian doesn't work efficiently over the whole array.
            return stats.mad_std(data, **kwargs)
        else:
            # Rechunk so that there is only one chunk along the desired axis
            data = data.rechunk([-1 if i == axis else 'auto' for i in range(3)])
            return self._compute(data.map_blocks(stats.mad_std, drop_axis=axis, axis=axis, **kwargs))

    @warn_slow
    @projection_if_needed
    @ignore_deprecated_kwargs
    def max(self, axis=None, **kwargs):
        """
        Return the maximum data value of the cube, optionally over an axis.
        """
        return self._compute(da.nanmax(self._get_filled_data(fill=np.nan), axis=axis, **kwargs))

    @warn_slow
    @projection_if_needed
    @ignore_deprecated_kwargs
    def min(self, axis=None, **kwargs):
        """
        Return the minimum data value of the cube, optionally over an axis.
        """
        return self._compute(da.nanmin(self._get_filled_data(fill=np.nan), axis=axis, **kwargs))

    @warn_slow
    @ignore_deprecated_kwargs
    def argmax(self, axis=None, **kwargs):
        """
        Return the index of the maximum data value.

        The return value is arbitrary if all pixels along ``axis`` are
        excluded from the mask.
        """
        return self._compute(da.nanargmax(self._get_filled_data(fill=-np.inf), axis=axis, **kwargs))

    @warn_slow
    @ignore_deprecated_kwargs
    def argmin(self, axis=None, **kwargs):
        """
        Return the index of the minimum data value.

        The return value is arbitrary if all pixels along ``axis`` are
        excluded from the mask.
        """
        return self._compute(da.nanargmin(self._get_filled_data(fill=np.inf), axis=axis))

    def _map_blocks_to_cube(self, function, additional_arrays=None, fill=np.nan, rechunk=None, **kwargs):
        """
        Call dask's map_blocks, returning a new spectral cube.
        """

        data = self._get_filled_data(fill=fill)

        if rechunk is not None:
            data = self._get_filled_data(fill=fill).rechunk(rechunk)

        if additional_arrays is None:
            newdata = data.map_blocks(function, dtype=data.dtype, **kwargs)
        else:
            additional_arrays = [array.rechunk(data.chunksize) for array in additional_arrays]
            newdata = da.map_blocks(function, data, *additional_arrays, dtype=data.dtype, **kwargs)

        # Create final output cube
        newcube = self._new_cube_with(data=newdata,
                                      wcs=self.wcs,
                                      mask=self.mask,
                                      meta=self.meta,
                                      fill_value=self.fill_value)

        return newcube

    # NOTE: the following three methods could also be implemented spaxel by
    # spaxel using apply_function_parallel_spectral but then take longer (but
    # less memory)

    def sigma_clip_spectrally(self,
                              threshold,
                              verbose=0,
                              num_cores=None,
                              **kwargs):
        """
        Run astropy's sigma clipper along the spectral axis, converting all bad
        (excluded) values to NaN.

        Parameters
        ----------
        threshold : float
            The ``sigma`` parameter in `astropy.stats.sigma_clip`, which refers
            to the number of sigma above which to cut.
        verbose : int
            Verbosity level to pass to joblib

        """

        def spectral_sigma_clip(array):
            result = stats.sigma_clip(array, sigma=threshold, axis=0, **kwargs)
            return result.filled(np.nan)

        # Rechunk so that there is only one chunk spectrally and let dask
        # decide for the rest
        return self._map_blocks_to_cube(spectral_sigma_clip,
                                        rechunk=(-1, 'auto', 'auto'))

    def spectral_smooth(self,
                        kernel,
                        convolve=convolution.convolve,
                        verbose=0,
                        num_cores=None,
                        **kwargs):
        """
        Smooth the cube along the spectral dimension

        Note that the mask is left unchanged in this operation.

        Parameters
        ----------
        kernel : `~astropy.convolution.Kernel1D`
            A 1D kernel from astropy
        convolve : function
            The astropy convolution function to use, either
            `astropy.convolution.convolve` or
            `astropy.convolution.convolve_fft`
        verbose : int
            Verbosity level to pass to joblib
        kwargs : dict
            Passed to the convolve function
        """

        if isinstance(kernel.array, u.Quantity):
            raise u.UnitsError("The convolution kernel should be defined "
                               "without a unit.")

        def spectral_smooth(array):
            if array.size > 0:
                kernel_3d = kernel.array.reshape((len(kernel.array), 1, 1))
                return convolve(array, kernel_3d, normalize_kernel=True)
            else:
                return array

        # Rechunk so that there is only one chunk spectrally
        return self._map_blocks_to_cube(spectral_smooth,
                                        rechunk=(-1, 'auto', 'auto'))

    def spectral_smooth_median(self, ksize,
                               use_memmap=True,
                               verbose=0,
                               num_cores=None,
                               **kwargs):
        """
        Smooth the cube along the spectral dimension

        Parameters
        ----------
        ksize : int
            Size of the median filter (scipy.ndimage.filters.median_filter)
        verbose : int
            Verbosity level to pass to joblib
        kwargs : dict
            Not used at the moment.
        """

        if not SCIPY_INSTALLED:
            raise ImportError("Scipy could not be imported: this function won't work.")

        if float(ksize).is_integer():
            ksize = int(ksize)
        else:
            raise TypeError('ksize should be an integer (got {0})'.format(ksize))

        def median_filter_wrapper(img, **kwargs):
            return ndimage.median_filter(img, (ksize, 1, 1), **kwargs)

        # Rechunk so that there is only one chunk spectrally
        return self._map_blocks_to_cube(median_filter_wrapper,
                                        rechunk=(-1, 'auto', 'auto'))

    def spatial_smooth(self, kernel,
                       convolve=convolution.convolve,
                       **kwargs):
        """
        Smooth the image in each spatial-spatial plane of the cube.

        Parameters
        ----------
        kernel : `~astropy.convolution.Kernel2D`
            A 2D kernel from astropy
        convolve : function
            The astropy convolution function to use, either
            `astropy.convolution.convolve` or
            `astropy.convolution.convolve_fft`
        kwargs : dict
            Passed to the convolve function
        """

        kernel = kernel.array.reshape((1,) + kernel.array.shape)

        def convolve_wrapper(img, **kwargs):
            if img.size > 0:
                return convolve(img, kernel, normalize_kernel=True, **kwargs)
            else:
                return img

        # Rechunk so that there is only one chunk in the image plane
        return self._map_blocks_to_cube(convolve_wrapper,
                                        rechunk=('auto', -1, -1))

    def spatial_smooth_median(self, ksize, **kwargs):
        """
        Smooth the image in each spatial-spatial plane of the cube using a median filter.

        Parameters
        ----------
        ksize : int
            Size of the median filter (scipy.ndimage.filters.median_filter)
        kwargs : dict
            Passed to the median_filter function
        """

        if not SCIPY_INSTALLED:
            raise ImportError("Scipy could not be imported: this function won't work.")

        def median_filter_wrapper(img, **kwargs):
            return ndimage.median_filter(img, (1, ksize, ksize), **kwargs)

        # Rechunk so that there is only one chunk in the image plane
        return self._map_blocks_to_cube(median_filter_wrapper,
                                        rechunk=('auto', -1, -1))

    def _pix_cen(self, axis=None):
        """
        Offset of every pixel from the origin, along each direction

        Returns
        -------
        tuple of spectral_offset, y_offset, x_offset, each 3D arrays
        describing the distance from the origin

        Notes
        -----
        These arrays are broadcast, and are not memory intensive

        Each array is in the units of the corresponding wcs.cunit, but
        this is implicit (e.g., they are not astropy Quantity arrays)
        """
        # Start off by extracting the world coordinates of the pixels
        _, lat, lon = self.world[0, :, :]
        spectral, _, _ = self.world[:, 0, 0]
        spectral -= spectral[0] # offset from first pixel

        # Convert to radians
        lon = np.radians(lon)
        lat = np.radians(lat)

        # Find the dx and dy arrays
        from astropy.coordinates.angle_utilities import angular_separation
        dx = angular_separation(lon[:, :-1], lat[:, :-1],
                                lon[:, 1:], lat[:, :-1])
        dy = angular_separation(lon[:-1, :], lat[:-1, :],
                                lon[1:, :], lat[1:, :])

        # Find the cumulative offset - need to add a zero at the start
        x = np.zeros(self._data.shape[1:])
        y = np.zeros(self._data.shape[1:])
        x[:, 1:] = np.cumsum(np.degrees(dx), axis=1)
        y[1:, :] = np.cumsum(np.degrees(dy), axis=0)

        x, y, spectral = da.broadcast_arrays(x[None,:,:], y[None,:,:], spectral[:,None,None])

        # NOTE: we need to rechunk these to the actual data size, otherwise
        # the resulting arrays have a single chunk which can cause issues with
        # da.store (which writes data out in chunks)
        return (spectral.rechunk(self._data.chunksize),
                y.rechunk(self._data.chunksize),
                x.rechunk(self._data.chunksize))

    def moment(self, order=0, axis=0, **kwargs):
        """
        Compute moments along the spectral axis.

        Moments are defined as follows:

        Moment 0:

        .. math:: M_0 \\int I dl

        Moment 1:

        .. math:: M_1 = \\frac{\\int I l dl}{M_0}

        Moment N:

        .. math:: M_N = \\frac{\\int I (l - M_1)^N dl}{M_0}

        .. warning:: Note that these follow the mathematical definitions of
                     moments, and therefore the second moment will return a
                     variance map. To get linewidth maps, you can instead use
                     the :meth:`~SpectralCube.linewidth_fwhm` or
                     :meth:`~SpectralCube.linewidth_sigma` methods.

        Parameters
        ----------
        order : int
           The order of the moment to take. Default=0

        axis : int
           The axis along which to compute the moment. Default=0

        Returns
        -------
        map [, wcs]
           The moment map (numpy array) and, if wcs=True, the WCS object
           describing the map

        Notes
        -----
        For the first moment, the result for axis=1, 2 is the angular
        offset *relative to the cube face*. For axis=0, it is the
        *absolute* velocity/frequency of the first moment.
        """

        if axis == 0 and order == 2:
            warnings.warn("Note that the second moment returned will be a "
                          "variance map. To get a linewidth map, use the "
                          "SpectralCube.linewidth_fwhm() or "
                          "SpectralCube.linewidth_sigma() methods instead.",
                          VarianceWarning)

        data = self._get_filled_data(fill=np.nan).astype(np.float64)
        pix_size = self._pix_size_slice(axis)
        pix_cen = self._pix_cen()[axis]

        if order == 0:
            out = nansum_allbadtonan(data * pix_size, axis=axis)
        else:
            denominator = self._compute(nansum_allbadtonan(data * pix_size, axis=axis))
            mom1 = (nansum_allbadtonan(data * pix_size * pix_cen, axis=axis) /
                    denominator)
            if order > 1:
                # insert an axis so it broadcasts properly
                shp = list(mom1.shape)
                shp.insert(axis, 1)
                mom1 = self._compute(mom1.reshape(shp))
                out = (nansum_allbadtonan(data * pix_size * (pix_cen - mom1) ** order, axis=axis) /
                       denominator)
            else:
                out = mom1

        # force computation, and convert back to original dtype (but native)
        out = self._compute(out)

        # apply units
        if order == 0:
            if axis == 0 and self._spectral_unit is not None:
                axunit = unit = self._spectral_unit
            else:
                axunit = unit = u.Unit(self._wcs.wcs.cunit[np2wcs[axis]])
            out = u.Quantity(out, self.unit * axunit, copy=False)
        else:
            if axis == 0 and self._spectral_unit is not None:
                unit = self._spectral_unit ** max(order, 1)
            else:
                unit = u.Unit(self._wcs.wcs.cunit[np2wcs[axis]]) ** max(order, 1)
            out = u.Quantity(out, unit, copy=False)

        # special case: for order=1, axis=0, you usually want
        # the absolute velocity and not the offset
        if order == 1 and axis == 0:
            out += self.world[0, :, :][0]

        new_wcs = wcs_utils.drop_axis(self._wcs, np2wcs[axis])

        meta = {'moment_order': order,
                'moment_axis': axis}
        meta.update(self._meta)

        return Projection(out, copy=False, wcs=new_wcs, meta=meta,
                          header=self._nowcs_header)

    def subcube_slices_from_mask(self, region_mask, spatial_only=False):
        """
        Given a mask, return the slices corresponding to the minimum subcube
        that encloses the mask

        Parameters
        ----------
        region_mask: `~spectral_cube.masks.MaskBase` or boolean `numpy.ndarray`
            The mask with appropriate WCS or an ndarray with matched
            coordinates
        spatial_only: bool
            Return only slices that affect the spatial dimensions; the spectral
            dimension will be left unchanged
        """

        if isinstance(region_mask, np.ndarray):
            if is_broadcastable_and_smaller(region_mask.shape, self.shape):
                region_mask = BooleanArrayMask(region_mask, self._wcs)
            else:
                raise ValueError("Mask shape does not match cube shape.")

        include = region_mask.include(self._data, self._wcs,
                                      wcs_tolerance=self._wcs_tolerance)

        include = da.broadcast_to(include, self.shape)

        # NOTE: the approach in the base SpectralCube class is incorrect, if
        # there are multiple 'islands' of valid values in the cube, as this will
        # pick only the first one found by find_objects. Here we use a more
        # robust approach.

        slices = []
        for axis in range(3):
            collapse_axes = tuple(index for index in range(3) if index != axis)
            valid = self._compute(da.any(include, axis=collapse_axes))
            if np.any(valid):
                indices = np.where(valid)[0]
                slices.append(slice(np.min(indices), np.max(indices) + 1))
            else:
                slices.append(slice(0))

        if spatial_only:
            slices = (slice(None), slices[1], slices[2])

        return tuple(slices)

    def flattened(self, slice=(), weights=None):
        """
        Return a slice of the cube giving only the valid data (i.e., removing
        bad values)

        Parameters
        ----------
        slice: 3-tuple
            A length-3 tuple of view (or any equivalent valid slice of a
            cube)
        weights: (optional) np.ndarray
            An array with the same shape (or slicing abilities/results) as the
            data cube
        """
        data = self._mask._flattened(data=self._data, wcs=self._wcs, view=slice)
        # Quantity does not work well with lazily evaluated data with an
        # unkonwn shape (which is the case when doing boolean indexing of arrays)
        if isinstance(data, da.Array):
            data = self._compute(data)
        if weights is not None:
            weights = self._mask._flattened(data=weights, wcs=self._wcs, view=slice)
            return u.Quantity(data * weights, self.unit, copy=False)
        else:
            return u.Quantity(data, self.unit, copy=False)

    @warn_slow
    def downsample_axis(self, factor, axis, estimator=np.nanmean,
                        truncate=False, use_memmap=True, progressbar=True):
        """
        Downsample the cube by averaging over *factor* pixels along an axis.
        Crops right side if the shape is not a multiple of factor.

        The WCS will be 'downsampled' by the specified factor as well.
        If the downsample factor is odd, there will be an offset in the WCS.

        There is both an in-memory and a memory-mapped implementation; the
        default is to use the memory-mapped version.  Technically, the 'large
        data' warning doesn't apply when using the memory-mapped version, but
        the warning is still there anyway.

        Parameters
        ----------
        myarr : `~numpy.ndarray`
            The array to downsample
        factor : int
            The factor to downsample by
        axis : int
            The axis to downsample along
        estimator : function
            defaults to mean.  You can downsample by summing or
            something else if you want a different estimator
            (e.g., downsampling error: you want to sum & divide by sqrt(n))
        truncate : bool
            Whether to truncate the last chunk or average over a smaller number.
            e.g., if you downsample [1,2,3,4] by a factor of 3, you could get either
            [2] or [2,4] if truncate is True or False, respectively.
        """

        # FIXME: this does not work correctly currently due to
        # https://github.com/dask/dask/issues/6102

        data = self._get_filled_data(fill=self._fill_value)
        mask = da.asarray(self.mask.include(), name=str(uuid.uuid4()))

        if not truncate and data.shape[axis] % factor != 0:
            padding_shape = list(data.shape)
            padding_shape[axis] = factor - data.shape[axis] % factor
            data_padding = da.ones(padding_shape) * np.nan
            mask_padding = da.zeros(padding_shape, dtype=bool)
            data = da.concatenate([data, data_padding], axis=axis)
            mask = da.concatenate([mask, mask_padding], axis=axis).rechunk()

        data = da.coarsen(estimator, data, {axis: factor}, trim_excess=True)
        mask = da.coarsen(estimator, mask, {axis: factor}, trim_excess=True)

        view = [slice(None, None, factor) if ii == axis else slice(None) for ii in range(self.ndim)]
        newwcs = wcs_utils.slice_wcs(self.wcs, view, shape=self.shape)
        newwcs._naxis = list(self.shape)

        # this is an assertion to ensure that the WCS produced is valid
        # (this is basically a regression test for #442)
        assert newwcs[:, slice(None), slice(None)]
        assert len(newwcs._naxis) == 3

        return self._new_cube_with(data=data, wcs=newwcs,
                                   mask=BooleanArrayMask(mask, wcs=newwcs))

    def spectral_interpolate(self, spectral_grid,
                             suppress_smooth_warning=False,
                             fill_value=None,
                             update_function=None):
        """Resample the cube spectrally onto a specific grid

        Parameters
        ----------
        spectral_grid : array
            An array of the spectral positions to regrid onto
        suppress_smooth_warning : bool
            If disabled, a warning will be raised when interpolating onto a
            grid that does not nyquist sample the existing grid.  Disable this
            if you have already appropriately smoothed the data.
        fill_value : float
            Value for extrapolated spectral values that lie outside of
            the spectral range defined in the original data.  The
            default is to use the nearest spectral channel in the
            cube.
        update_function : method
            Method that is called to update an external progressbar
            If provided, it disables the default `astropy.utils.console.ProgressBar`

        Returns
        -------
        cube : SpectralCube

        """

        inaxis = self.spectral_axis.to(spectral_grid.unit)

        indiff = np.mean(np.diff(inaxis))
        outdiff = np.mean(np.diff(spectral_grid))

        reverse_in = indiff < 0
        reverse_out = outdiff < 0

        # account for reversed axes

        if reverse_in:
            inaxis = inaxis[::-1]
            indiff = np.mean(np.diff(inaxis))

        if reverse_out:
            spectral_grid = spectral_grid[::-1]
            outdiff = np.mean(np.diff(spectral_grid))

        cubedata = self._get_filled_data(fill=np.nan)

        # insanity checks
        if indiff < 0 or outdiff < 0:
            raise ValueError("impossible.")

        assert np.all(np.diff(spectral_grid) > 0)
        assert np.all(np.diff(inaxis) > 0)

        np.testing.assert_allclose(np.diff(spectral_grid), outdiff,
                                   err_msg="Output grid must be linear")

        if outdiff > 2 * indiff and not suppress_smooth_warning:
            warnings.warn("Input grid has too small a spacing. The data should "
                          "be smoothed prior to resampling.", SmoothingWarning)

        def interp_wrapper(y, args):
            if y.size == 1:
                return y
            else:
                return np.interp(args[0], args[1], y[:, 0, 0],
                                 left=fill_value, right=fill_value).reshape((-1, 1, 1))

        if reverse_in:
            cubedata = cubedata[::-1, :, :]

        cubedata = cubedata.rechunk((-1, 1, 1))

        newcube = cubedata.map_blocks(interp_wrapper,
                                      args=(spectral_grid.value, inaxis.value),
                                      chunks=(len(spectral_grid), 1, 1))

        newwcs = self.wcs.deepcopy()
        newwcs.wcs.crpix[2] = 1
        newwcs.wcs.crval[2] = spectral_grid[0].value if not reverse_out \
            else spectral_grid[-1].value
        newwcs.wcs.cunit[2] = spectral_grid.unit.to_string('FITS')
        newwcs.wcs.cdelt[2] = outdiff.value if not reverse_out \
            else -outdiff.value
        newwcs.wcs.set()

        newbmask = BooleanArrayMask(~np.isnan(newcube), wcs=newwcs)

        if reverse_out:
            newcube = newcube[::-1, :, :]

        newcube = self._new_cube_with(data=newcube, wcs=newwcs, mask=newbmask,
                                      meta=self.meta,
                                      fill_value=self.fill_value)

        return newcube


class DaskSpectralCube(DaskSpectralCubeMixin, SpectralCube):

    def __init__(self, data, *args, **kwargs):
        if not isinstance(data, da.Array):
            # NOTE: don't be tempted to chunk this image-wise (following the
            # data storage) because spectral operations will take forever.
            data = da.asarray(data, name=str(uuid.uuid4()))
        super().__init__(data, *args, **kwargs)

    @classmethod
    def read(cls, *args, **kwargs):
        if kwargs.get('use_dask') is None:
            kwargs['use_dask'] = True
        return super().read(*args, **kwargs)

    @property
    def hdu(self):
        """
        HDU version of self
        """
        return PrimaryHDU(self._get_filled_data(fill=self._fill_value), header=self.header)

    @property
    def hdulist(self):
        return HDUList(self.hdu)

    def convolve_to(self, beam, convolve=convolution.convolve, update_function=None, **kwargs):
        """
        Convolve each channel in the cube to a specified beam

        Parameters
        ----------
        beam : `radio_beam.Beam`
            The beam to convolve to
        convolve : function
            The astropy convolution function to use, either
            `astropy.convolution.convolve` or
            `astropy.convolution.convolve_fft`
        update_function : method
            Method that is called to update an external progressbar
            If provided, it disables the default `astropy.utils.console.ProgressBar`
        kwargs : dict
            Keyword arguments to pass to the convolution function

        Returns
        -------
        cube : `SpectralCube`
            A SpectralCube with a single ``beam``
        """

        # Check if the beams are the same.
        if beam == self.beam:
            warnings.warn("The given beam is identical to the current beam. "
                          "Skipping convolution.")
            return self

        pixscale = proj_plane_pixel_area(self.wcs.celestial)**0.5 * u.deg

        convolution_kernel = beam.deconvolve(self.beam).as_kernel(pixscale)
        kernel = convolution_kernel.array.reshape((1,) + convolution_kernel.array.shape)

        def convfunc(img):
            if img.size > 0:
                return convolve(img, kernel, normalize_kernel=True, **kwargs).reshape(img.shape)
            else:
                return img

        # Rechunk so that there is only one chunk in the image plane
        return self._map_blocks_to_cube(convfunc,
                                        rechunk=('auto', -1, -1)).with_beam(beam)


class DaskVaryingResolutionSpectralCube(DaskSpectralCubeMixin, VaryingResolutionSpectralCube):

    def __init__(self, data, *args, **kwargs):
        if not isinstance(data, da.Array):
            # NOTE: don't be tempted to chunk this image-wise (following the
            # data storage) because spectral operations will take forever.
            data = da.asarray(data, name=str(uuid.uuid4()))
        super().__init__(data, *args, **kwargs)

    @property
    def hdu(self):
        raise ValueError("For DaskVaryingResolutionSpectralCube's, use hdulist "
                         "instead of hdu.")

    @property
    def hdulist(self):
        """
        HDUList version of self
        """

        hdu = PrimaryHDU(self._get_filled_data(fill=self._fill_value), header=self.header)

        from .cube_utils import beams_to_bintable
        # use unmasked beams because, even if the beam is masked out, we should
        # write it
        bmhdu = beams_to_bintable(self.unmasked_beams)

        return HDUList([hdu, bmhdu])

    def convolve_to(self, beam, allow_smaller=False,
                    convolve=convolution.convolve_fft,
                    update_function=None, **kwargs):
        """
        Convolve each channel in the cube to a specified beam

        .. warning::
            The current implementation of ``convolve_to`` creates an in-memory
            copy of the whole cube to store the convolved data.  Issue #506
            notes that this is a problem, and it is on our to-do list to fix.

        .. warning::
            Note that if there is any misaligment between the cube's spatial
            pixel axes and the WCS's spatial axes *and* the beams are not
            round, the convolution kernels used here may be incorrect.  Be wary
            in such cases!

        Parameters
        ----------
        beam : `radio_beam.Beam`
            The beam to convolve to
        allow_smaller : bool
            If the specified target beam is smaller than the beam in a channel
            in any dimension and this is ``False``, it will raise an exception.
        convolve : function
            The astropy convolution function to use, either
            `astropy.convolution.convolve` or
            `astropy.convolution.convolve_fft`
        update_function : method
            Method that is called to update an external progressbar
            If provided, it disables the default `astropy.utils.console.ProgressBar`

        Returns
        -------
        cube : `SpectralCube`
            A SpectralCube with a single ``beam``
        """


        if ((self.wcs.celestial.wcs.get_pc()[0,1] != 0 or
             self.wcs.celestial.wcs.get_pc()[1,0] != 0)):
            warnings.warn("The beams will produce convolution kernels "
                          "that are not aware of any misaligment "
                          "between pixel and world coordinates, "
                          "and there are off-diagonal elements of the "
                          "WCS spatial transformation matrix.  "
                          "Unexpected results are likely.",
                          BeamWarning
                         )

        pixscale = wcs.utils.proj_plane_pixel_area(self.wcs.celestial)**0.5*u.deg

        beams = []
        for bm, valid in zip(self.unmasked_beams, self.goodbeams_mask):
            if not valid:
                # just skip masked-out beams
                beams.append(None)
                continue
            elif beam == bm:
                # Point response when beams are equal, don't convolve.
                beams.append(None)
                continue
            try:
                beams.append(beam.deconvolve(bm))
            except ValueError:
                if allow_smaller:
                    beams.append(None)
                else:
                    raise

        # We need to pass in the beams to dask, so we hide them inside an object array
        # that can then be chunked like the data.
        beams = da.from_array(np.array(beams, dtype=np.object)
                              .reshape((len(beams), 1, 1)), chunks=(-1, -1, -1))

        def convfunc(img, beam):
            if img.size > 0:
                out = np.zeros(img.shape, dtype=img.dtype)
                for index in range(img.shape[0]):
                    kernel = beam[index, 0, 0].as_kernel(pixscale)
                    out[index] = convolve(img[index], kernel, normalize_kernel=True, **kwargs)
                return out
            else:
                return img

        # Rechunk so that there is only one chunk in the image plane
        cube = self._map_blocks_to_cube(convfunc,
                                        additional_arrays=(beams,),
                                        rechunk=('auto', -1, -1))

        # Result above is a DaskVaryingResolutionSpectralCube, convert to DaskSpectralCube
        newcube = DaskSpectralCube(data=cube._data,
                                   beam=beam,
                                   wcs=cube.wcs,
                                   mask=cube.mask,
                                   meta=cube.meta,
                                   fill_value=cube.fill_value)

        return newcube

    def spectral_interpolate(self, *args, **kwargs):
        raise AttributeError("VaryingResolutionSpectralCubes can't be "
                             "spectrally interpolated.  Convolve to a "
                             "common resolution with `convolve_to` before "
                             "attempting spectral interpolation.")

    def spectral_smooth(self, *args, **kwargs):
        raise AttributeError("VaryingResolutionSpectralCubes can't be "
                             "spectrally smoothed.  Convolve to a "
                             "common resolution with `convolve_to` before "
                             "attempting spectral smoothed.")

    @property
    def _mask_include(self):
        return da.from_array(MaskHandler(self), name='MaskHandler ' + str(uuid.uuid4()), chunks=self._data.chunksize)
