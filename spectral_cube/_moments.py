import numpy as np


def _moment_shp(cube, axis):
    return cube.shape[:axis] + cube.shape[axis + 1:]


def _slice0(cube, axis):
    shp = _moment_shp(cube, axis)
    result = np.zeros(shp)

    view = [slice(None)] * 3
    pix_size = cube._pix_size()[axis]

    valid = np.zeros(shp, dtype=np.bool)
    for i in range(cube.shape[axis]):
        view[axis] = i
        plane = cube.get_data(slices=view)
        valid |= np.isfinite(plane)
        result += np.nan_to_num(plane) * pix_size[view]
    result[~valid] = np.nan
    return result


def _slice1(cube, axis):
    shp = _moment_shp(cube, axis)
    result = np.zeros(shp)

    view = [slice(None)] * 3
    pix_size = cube._pix_size()[axis]
    pix_cen = cube._pix_cen()[axis]
    weights = np.zeros(shp)

    for i in range(cube.shape[axis]):
        view[axis] = i
        plane = cube.get_data(fill=0, slices=view)
        result += (plane *
                   pix_cen[view] *
                   pix_size[view])
        weights += plane * pix_size[view]
    return result / weights


def moment_slicewise(cube, order, axis):
    """
    Compute moments by accumulating the result 1 slice at a time
    """
    if order == 0:
        return _slice0(cube, axis)
    if order == 1:
        return _slice1(cube, axis)

    shp = _moment_shp(cube, axis)
    result = np.zeros(shp)

    view = [slice(None)] * 3
    pix_size = cube._pix_size()[axis]
    pix_cen = cube._pix_cen()[axis]
    weights = np.zeros(shp)

    # would be nice to get mom1 and momn in single pass over data
    # possible for mom2, not sure about general case
    mom1 = _slice1(cube, axis)

    for i in range(cube.shape[axis]):
        view[axis] = i
        plane = cube.get_data(fill=0, slices=view)
        result += (plane *
                   (pix_cen[view] - mom1) ** order *
                   pix_size[view])
        weights += plane * pix_size[view]

    return (result / weights)


def moment_raywise(cube, order, axis):
    """
    Compute moments by accumulating the answer one ray at a time
    """
    shp = _moment_shp(cube, axis)
    out = np.zeros(shp) * np.nan

    pix_cen = cube._pix_cen()[axis]
    pix_size = cube._pix_size()[axis]

    for x, y, slc in cube._iter_rays(axis):
        # the intensity, i.e. the weights
        include = cube._mask.include(data=cube._data, wcs=cube._wcs,
                                     slices=slc)
        if not include.any():
            continue

        data = cube.flattened(slc) * pix_size[slc][include]

        if order == 0:
            out[x, y] = data.sum()
            continue

        order1 = (data * pix_cen[slc][include]).sum() / data.sum()
        if order == 1:
            out[x, y] = order1
            continue

        ordern = (data * (pix_cen[slc][include] - order1) ** order).sum()
        ordern /= data.sum()

        out[x, y] = ordern
    return out


def moment_cubewise(cube, order, axis):
    """
    Compute the moments by working with the entire data at once
    """

    pix_cen = cube._pix_cen()[axis]
    data = cube.get_data() * cube._pix_size()[axis]

    if order == 0:
        return np.nansum(data, axis=axis)

    if order == 1:
        return (np.nansum(data * pix_cen, axis=axis) /
                np.nansum(data, axis=axis))
    else:
        mom1 = moment_cubewise(cube, 1, axis)
        return (np.nansum(data * (pix_cen - mom1) ** order, axis=axis) /
                np.nansum(data, axis=axis))


def moment_auto(cube, order, axis):
    # guess a good strategy

    if np.product(cube.shape) < 1e7:  # smallish, do in RAM
        return moment_cubewise(cube, order, axis)

    return moment_slicewise(cube, order, axis)  # save memory
