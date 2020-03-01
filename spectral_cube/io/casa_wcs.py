import numpy as np
from astropy.wcs import WCS
from astropy.io import fits
from astropy.time import Time

__all__ = ['wcs_casa2astropy']


def sanitize_unit(unit):
    if unit == "'":
        return 'arcmin'
    else:
        return unit


def wcs_casa2astropy(coordsys):
    """
    Convert a casac.coordsys object into an astropy.wcs.WCS object
    """

    # Rather than try and parse the CASA coordsys ourselves, we delegate
    # to CASA by getting it to write out a FITS file and reading back in
    # using WCS

    from pprint import pprint
    pprint(coordsys)

    header = fits.Header()

    # Observer information (ObsInfo.cc)

    header['OBSERVER'] = coordsys['observer']
    header['TELESCOP'] = coordsys['telescope']
    header['TIMESYS'] = coordsys['obsdate']['refer']
    header['MJD-OBS'] = coordsys['obsdate']['m0']['value']

    dateobs = Time(header['MJD-OBS'],
                   format='mjd',
                   scale=coordsys['obsdate']['refer'].lower())
    dateobs.precision = 6
    header['DATE-OBS'] = dateobs.isot

    obsgeo_lon = coordsys['telescopeposition']['m0']['value']
    obsgeo_lat = coordsys['telescopeposition']['m1']['value']
    obsgeo_alt = coordsys['telescopeposition']['m2']['value']

    header['OBSGEO-X'] = obsgeo_alt * np.cos(obsgeo_lon) * np.cos(obsgeo_lat)
    header['OBSGEO-Y'] = obsgeo_alt * np.sin(obsgeo_lon) * np.cos(obsgeo_lat)
    header['OBSGEO-Z'] = obsgeo_alt * np.sin(obsgeo_lat)

    # World coordinates

    # Find worldmap entries

    worldmap = {}

    for key, value in coordsys.items():
        if key.startswith('worldmap'):
            index = int(key[8:])
            worldmap[index] = value

    # Now iterate through the different coordinate types to populate the WCS

    header['WCSAXES'] = np.max([np.max(idx) + 1 for idx in worldmap.values()])

    # Initialize PC
    for i in range(header['WCSAXES']):
        for j in range(header['WCSAXES']):
            header[f'PC{i+1}_{j+1}'] = 0.

    for coord_type in ('direction', 'spectral', 'stokes', 'linear'):

        for index in range(len(worldmap)):
            if f'{coord_type}{index}' in coordsys:
                break
        else:
            continue

        data = coordsys[f'{coord_type}{index}']

        AXES_TO_CTYPE = {}
        AXES_TO_CTYPE['Right Ascension'] = 'RA--'
        AXES_TO_CTYPE['Declination'] = 'DEC-'
        AXES_TO_CTYPE['Stokes'] = 'STOKES'
        AXES_TO_CTYPE['Frequency'] = 'FREQ'

        SYSTEM_TO_SPECSYS = {}
        SYSTEM_TO_SPECSYS['BARY'] = 'BARYCENT'
        SYSTEM_TO_SPECSYS['LSRK'] = 'LSRK'

        RADESYS = {}
        RADESYS['J2000'] = 'FK5'
        RADESYS['B1950'] = 'FK4'
        RADESYS['B1950_VLA'] = 'FK4'
        RADESYS['ICRS'] = 'ICRS'

        EQUINOX = {}
        EQUINOX['J2000'] = 2000.
        EQUINOX['B1950'] = 1950.
        EQUINOX['B1950_VLA'] = 1979.9

        if coord_type == 'direction':
            idx1, idx2 = worldmap[index] + 1
            header[f'CTYPE{idx1}'] = AXES_TO_CTYPE[data['axes'][0]] + '-' + data['projection']
            header[f'CTYPE{idx2}'] = AXES_TO_CTYPE[data['axes'][1]] + '-' + data['projection']
            header[f'CRPIX{idx1}'] = data['crpix'][0] + 1
            header[f'CRPIX{idx2}'] = data['crpix'][1] + 1
            header[f'CRVAL{idx1}'] = data['crval'][0]
            header[f'CRVAL{idx2}'] = data['crval'][1]
            header[f'CDELT{idx1}'] = data['cdelt'][0]
            header[f'CDELT{idx2}'] = data['cdelt'][1]
            header[f'CUNIT{idx1}'] = sanitize_unit(data['units'][0])
            header[f'CUNIT{idx2}'] = sanitize_unit(data['units'][1])
            header['LONPOLE'] = data['longpole']
            header['LATPOLE'] = data['latpole']
            header['RADESYS'] = RADESYS[data['conversionSystem']]
            if data['conversionSystem'] in EQUINOX:
                header['EQUINOX'] = EQUINOX[data['conversionSystem']]
            # NOTE: unclear if it is deliberate that the following is always
            # ?_2 and ?_1 or whether it should depend on the index of the
            # longitude.
            header[f'PV{idx2}_1'] = 0.
            header[f'PV{idx2}_2'] = 0.
            header[f'PC{idx1}_{idx1}'] = data['pc'][0, 0]
            header[f'PC{idx1}_{idx2}'] = data['pc'][0, 1]
            header[f'PC{idx2}_{idx1}'] = data['pc'][1, 0]
            header[f'PC{idx2}_{idx2}'] = data['pc'][1, 1]
        elif coord_type == 'stokes':
            idx = worldmap[index][0] + 1
            header[f'CTYPE{idx}'] = AXES_TO_CTYPE[data['axes'][0]]
            header[f'CRVAL{idx}'] = data['crval'][0]
            header[f'CRPIX{idx}'] = data['crpix'][0] + 1
            header[f'CDELT{idx}'] = data['cdelt'][0]
            header[f'CUNIT{idx}'] = ''
            header[f'PC{idx}_{idx}'] = data['pc'][0][0]
        elif coord_type == 'spectral':
            idx = worldmap[index][0] + 1
            if 'tabular' in data:
                header[f'CTYPE{idx}'] = AXES_TO_CTYPE[data['tabular']['axes'][0]]
                header[f'CRVAL{idx}'] = data['tabular']['crval'][0]
                header[f'CRPIX{idx}'] = data['tabular']['crpix'][0] + 1
                header[f'CDELT{idx}'] = data['tabular']['cdelt'][0]
                header[f'CUNIT{idx}'] = data['tabular']['units'][0]
            else:
                header[f'CTYPE{idx}'] = data['wcs']['ctype']
                header[f'CRVAL{idx}'] = data['wcs']['crval']
                header[f'CRPIX{idx}'] = data['wcs']['crpix'] + 1
                header[f'CDELT{idx}'] = data['wcs']['cdelt']
                header[f'CUNIT{idx}'] = data['unit']
            header[f'PC{idx}_{idx}'] = 1.0
            header[f'RESTFRQ'] = data['restfreq']
            header[f'SPECSYS'] = SYSTEM_TO_SPECSYS[data['system']]
        elif coord_type == 'linear':
            idx1, idx2 = worldmap[index] + 1
            header[f'CTYPE{idx1}'] = data['axes'][0].upper()
            header[f'CTYPE{idx2}'] = data['axes'][1].upper()
            header[f'CRVAL{idx1}'] = data['crval'][0]
            header[f'CRVAL{idx2}'] = data['crval'][1]
            header[f'CRPIX{idx1}'] = data['crpix'][0] + 1
            header[f'CRPIX{idx2}'] = data['crpix'][1] + 1
            header[f'CDELT{idx1}'] = data['cdelt'][0]
            header[f'CDELT{idx2}'] = data['cdelt'][1]
            header[f'CUNIT{idx1}'] = data['units'][0]
            header[f'CUNIT{idx2}'] = data['units'][1]
            header[f'PC{idx1}_{idx1}'] = data['pc'][0, 0]
            header[f'PC{idx1}_{idx2}'] = data['pc'][0, 1]
            header[f'PC{idx2}_{idx1}'] = data['pc'][1, 0]
            header[f'PC{idx2}_{idx2}'] = data['pc'][1, 1]
        else:
            raise NotImplementedError(f'coord_type is {coord_type}')

    return WCS(header)
