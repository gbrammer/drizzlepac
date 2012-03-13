from __future__ import division # confidence medium

import pyfits
from stsci.tools import fileutil, readgeis, logutil

from fitsblender import blendheaders

yes = True
no = False

RESERVED_KEYS = ['NAXIS','BITPIX','DATE','IRAF-TLM','XTENSION','EXTNAME','EXTVER']

EXTLIST = ('SCI', 'WHT', 'CTX')

WCS_KEYWORDS=['CD1_1','CD1_2', 'CD2_1', 'CD2_2', 'CRPIX1',
'CRPIX2','CRVAL1', 'CRVAL2', 'CTYPE1', 'CTYPE2','WCSNAME']

# Set up dictionary of default keywords to be written out to the header
# of the output drizzle image using writeDrizKeywords()
DRIZ_KEYWORDS = {
                'VER':{'value':"",'comment':'Drizzle, task version'},
                'GEOM':{'value':"wcs",'comment':'Drizzle, source of geometric information'},
                'DATA':{'value':"",'comment':'Drizzle, input data image'},
                'DEXP':{'value':"",'comment':'Drizzle, input image exposure time (s)'},
                'OUDA':{'value':"",'comment':'Drizzle, output data image'},
                'OUWE':{'value':"",'comment':'Drizzle, output weighting image'},
                'OUCO':{'value':"",'comment':'Drizzle, output context image'},
                'MASK':{'value':"",'comment':'Drizzle, input weighting image'},
                'WTSC':{'value':"",'comment':'Drizzle, weighting factor for input image'},
                'KERN':{'value':"",'comment':'Drizzle, form of weight distribution kernel'},
                'PIXF':{'value':"1.0",'comment':'Drizzle, linear size of drop'},
                'COEF':{'value':"SIP",'comment':'Drizzle, source of coefficients'},
                'OUUN':{'value':"cps",'comment':'Drizzle, units of output image - counts or cps'},
                'FVAL':{'value':"INDEF",'comment':'Drizzle, fill value for zero weight output pix'},
                'WKEY':{'value':"",'comment':'Input image WCS Version used'}
                }


log = logutil.create_logger(__name__)


class OutputImage:
    """
    This class manages the creation of the array objects
    which will be used by Drizzle. The three arrays, SCI/WHT/CTX,
    will be setup either as extensions in a
    single multi-extension FITS file, or as separate FITS
    files.
    """
    def __init__(self, plist, input_pars, build=yes, wcs=None, single=no, blot=no):
        """
        The object 'plist' must contain at least the following members::

            plist['output']   - name of output FITS image (for SCI)
            plist['outnx']    - size of X axis for output array
            plist['outny']    - size of Y axis for output array

        If 'single=yes', then 'plist' also needs to contain::

            plist['outsingle']
            plist['outsweight']
            plist['outscontext']

        If 'blot=yes', then 'plist' also needs::

            plist['data']
            plist['blotImage']
            plist['blotnx'],plist['blotny']

        If 'build' is set to 'no', then each extension/array must be
        in separate FITS objects.  This would also require::

          plist['outdata']    - name of output SCI FITS image
          plist['outweight']  - name of output WHT FITS image
          plist['outcontext'] - name of output CTX FITS image

        Optionally, the overall exposure time information can be passed as::

            plist['texptime'] - total exptime for output
            plist['expstart'] - start time of combined exposure
            plist['expend']   - end time of combined exposure


        """
        self.build = build
        self.single = single
        self.parlist = plist
        self.input_pars = input_pars
        _nimgs = len(self.parlist)
        self.bunit = None
        self.units = 'cps'
        self.blot = blot

        # Merge input_pars with each chip's outputNames object
        for p in self.parlist:
            p.update(input_pars)

        if not blot:
            self.output = plist[0]['output']
            self.shape = (plist[0]['outny'],plist[0]['outnx'])
        else:
            self.output = plist[0]['blotImage']
            self.shape = (plist[0]['blotny'],plist[0]['blotnx'])

        # Keep track of desired output WCS computed by PyDrizzle
        self.wcs = wcs

        #
        # Apply special operating switches:
        #   single - separate output for each input
        #
        if single:
            _outdata = plist[0]['outSingle']
            _outweight = plist[0]['outSWeight']
            _outcontext = plist[0]['outSContext']
            # Only report values appropriate for single exposure
            self.texptime = plist[0]['texptime']
            self.expstart = plist[0]['texpstart']
            self.expend = plist[0]['texpend']
        else:
            if self.build:
                _outdata = plist[0]['outFinal']
            else:
                _outdata = plist[0]['outSci']
            _outweight = plist[0]['outWeight']
            _outcontext = plist[0]['outContext']
            # Report values appropriate for entire combined product
            self.texptime = plist[0]['texptime']
            self.expstart = plist[0]['texpstart']
            self.expend = plist[_nimgs-1]['texpend']


        if blot:
            _outdata = plist[0]['blotImage']

        if not self.build or single:
            self.output = _outdata

        self.outdata = _outdata
        self.outweight = _outweight
        self.outcontext = _outcontext

    def set_bunit(self,bunit):
        """
        Method used to update the value of the bunit attribute.
        """
        self.bunit = bunit

    def set_units(self,units):
        """
        Method used to record what units were specified by the user for the output product.
        """
        self.units = units

    def writeFITS(self, template, sciarr, whtarr, ctxarr=None, 
                versions=None, overwrite=yes, blend=True):
        """
        Generate PyFITS objects for each output extension
        using the file given by 'template' for populating
        headers.

        The arrays will have the size specified by 'shape'.
        """     
        if fileutil.findFile(self.output):
            if overwrite:
                log.info('Deleting previous output product: %s' % self.output)
                fileutil.removeFile(self.output)

            else:
                log.warning('Output file %s already exists and overwrite not '
                            'specified!' % self.output)
                log.error('Quitting... Please remove before resuming '
                          'operations.')
                raise IOError
            
        # Default value for NEXTEND when 'build'== True
        nextend = 3
        if not self.build:
            nextend = 0
            if self.outweight:
                if overwrite:
                    if fileutil.findFile(self.outweight):
                        log.info('Deleting previous output WHT product: %s' %
                                 self.outweight)
                    fileutil.removeFile(self.outweight)
                else:
                    log.warning('Output file %s already exists and overwrite '
                                'not specified!' % self.outweight)
                    log.error('Quitting... Please remove before resuming '
                              'operations.')
                    raise IOError


            if self.outcontext:
                if overwrite:
                    if fileutil.findFile(self.outcontext):
                        log.info('Deleting previous output CTX product: %s' %
                                 self.outcontext)
                    fileutil.removeFile(self.outcontext)
                else:
                    log.warning('Output file %s already exists and overwrite '
                                'not specified!' % self.outcontext)
                    log.error('Quitting... Please remove before resuming '
                              'operations.')
                    raise IOError


        # Get default headers from multi-extension FITS file
        # If only writing out single drizzle product, blending needs to be 
        # forced off as there is only 1 input to report, no blending needed 
        if self.single:
            blend=False

        # If input data is not in MEF FITS format, it will return 'None'
        # and those headers will have to be generated from drizzle output
        # file FITS headers.
        # NOTE: These are HEADER objects, not HDUs
        #prihdr,scihdr,errhdr,dqhdr = getTemplates(template)

        newhdrs, newtab = getTemplates(template,blend=blend)
        if newtab is not None: nextend += 1 # account for new table extn

        prihdr = newhdrs[0]
        scihdr = newhdrs[1]
        errhdr = newhdrs[2]
        dqhdr = newhdrs[3]
        
        # Setup primary header as an HDU ready for appending to output FITS file
        prihdu = pyfits.PrimaryHDU(header=prihdr,data=None)

        # Start by updating PRIMARY header keywords...
        prihdu.header.update('EXTEND',pyfits.TRUE,after='NAXIS')
        prihdu.header.update('NEXTEND',nextend)
        prihdu.header.update('FILENAME', self.output)

        # Update the ROOTNAME with the new value as well
        _indx = self.output.find('_drz')
        if _indx < 0:
            prihdu.header.update('ROOTNAME', self.output)
        else:
            prihdu.header.update('ROOTNAME', self.output[:_indx])


        # Get the total exposure time for the image
        # If not calculated by PyDrizzle and passed through
        # the pardict, then leave value from the template image.
        if self.texptime:
            prihdu.header.update('EXPTIME', self.texptime)
            prihdu.header.update('EXPSTART', self.expstart)
            prihdu.header.update('EXPEND', self.expend)

        #Update ASN_MTYPE to reflect the fact that this is a product
        # Currently hard-wired to always output 'PROD-DTH' as MTYPE
        prihdu.header.update('ASN_MTYP', 'PROD-DTH')

        # Update DITHCORR calibration keyword if present
        # Remove when we can modify FITS headers in place...
        if prihdu.header.has_key('DRIZCORR') > 0:
            prihdu.header['DRIZCORR'] = 'COMPLETE'
        if prihdu.header.has_key('DITHCORR') > 0:
            prihdu.header['DITHCORR'] = 'COMPLETE'

        prihdu.header.update('NDRIZIM',len(self.parlist),
            comment='Drizzle, No. images drizzled onto output')

        # Only a subset of these keywords makes sense for the new WCS based
        # transformations. They need to be reviewed to decide what to keep
        # and what to leave out.
        if not self.blot:
            self.addDrizKeywords(prihdu.header,versions)

        if scihdr:
            del scihdr['MDRIZSKY']
            del scihdr['OBJECT']
            if scihdr.has_key('CCDCHIP'): scihdr.update('CCDCHIP','-999')
            if scihdr.has_key('NCOMBINE') > 0:
                scihdr.update('NCOMBINE', self.parlist[0]['nimages'])

            # If BUNIT keyword was found and reset, then

            if self.bunit is not None:
                comment_str = "Units of science product"
                if self.bunit.lower()[:5] == 'count':
                    comment_str = "counts * gain = electrons"
                scihdr.update('BUNIT',self.bunit,comment=comment_str)
            else:
                # check to see whether to update already present BUNIT comment
                if scihdr.has_key('bunit') and scihdr['bunit'].lower()[:5] == 'count':
                    comment_str = "counts * gain = electrons"
                    scihdr.update('BUNIT',scihdr['bunit'],comment=comment_str)

            # Add WCS keywords to SCI header
            if self.wcs:
                addWCSKeywords(self.wcs,scihdr,blot=self.blot)

        ##########
        # Now, build the output file
        ##########
        if self.build:
            print '-Generating multi-extension output file: ',self.output
            fo = pyfits.HDUList()

            # Add primary header to output file...
            fo.append(prihdu)

            hdu = pyfits.ImageHDU(data=sciarr,header=scihdr,name=EXTLIST[0])
            hdu.header.update('EXTNAME','SCI')
            hdu.header.update('EXTVER',1)
            fo.append(hdu)

            # Build WHT extension here, if requested...
            if errhdr:
                errhdr.update('CCDCHIP','-999')

            hdu = pyfits.ImageHDU(data=whtarr,header=errhdr,name=EXTLIST[1])
            hdu.header.update('EXTVER',1)
            hdu.header.update('EXTNAME','WHT')
            if self.wcs:
                # Update WCS Keywords based on PyDrizzle product's value
                # since 'drizzle' itself doesn't update that keyword.
                addWCSKeywords(self.wcs,hdu.header,blot=self.blot)

            fo.append(hdu)

            # Build CTX extension here
            # If there is only 1 plane, write it out as a 2-D extension
            if self.outcontext:
                if ctxarr.shape[0] == 1:
                    _ctxarr = ctxarr[0]
                else:
                    _ctxarr = ctxarr
            else:
                _ctxarr = None

            hdu = pyfits.ImageHDU(data=_ctxarr,header=dqhdr,name=EXTLIST[2])
            hdu.header.update('EXTVER',1)
            hdu.header.update('EXTNAME','CTX')
            
            if self.wcs:
                # Update WCS Keywords based on PyDrizzle product's value
                # since 'drizzle' itself doesn't update that keyword.
                addWCSKeywords(self.wcs,hdu.header,blot=self.blot)
            fo.append(hdu)

            # add table of combined header keyword values to FITS file
            if newtab is not None:
                fo.append(newtab)
                
            # write out file to disk
            fo.writeto(self.output)
            fo.close()
            del fo, hdu

        else:
            print('-Generating simple FITS output: %s' % self.outdata)
            
            fo = pyfits.HDUList()

            hdu = pyfits.PrimaryHDU(data=sciarr, header=prihdu.header)
            # explicitly set EXTEND to FALSE for simple FITS files.
            dim = len(sciarr.shape)
            hdu.header.update('extend',pyfits.FALSE,after='NAXIS%s'%dim)

            # Append remaining unique header keywords from template DQ
            # header to Primary header...
            if scihdr:
                for _card in scihdr.ascard:
                    if _card.key not in RESERVED_KEYS and hdu.header.has_key(_card.key) == 0:
                        hdu.header.ascard.append(_card)
            del hdu.header['PCOUNT']
            del hdu.header['GCOUNT']
            hdu.header.update('filename',self.outdata)

            if self.wcs:
                # Add WCS keywords to header
                addWCSKeywords(self.wcs, hdu.header, blot=self.blot)

            # Add primary header to output file...
            fo.append(hdu)
            # add table of combined header keyword values to FITS file
            if newtab is not None:
                fo.append(newtab)
            # write out file to disk
            fo.writeto(self.outdata)
            del fo,hdu

            if self.outweight and whtarr != None:
                # We need to build new PyFITS objects for each WHT array
                fwht = pyfits.HDUList()

                if errhdr:
                    errhdr.update('CCDCHIP','-999')

                hdu = pyfits.PrimaryHDU(data=whtarr, header=prihdu.header)

                # Append remaining unique header keywords from template DQ
                # header to Primary header...
                if errhdr:
                    for _card in errhdr.ascard:
                        if _card.key not in RESERVED_KEYS and hdu.header.has_key(_card.key) == 0:
                            hdu.header.ascard.append(_card)
                hdu.header.update('filename',self.outweight)
                hdu.header.update('CCDCHIP','-999')
                if self.wcs:
                    # Update WCS Keywords based on PyDrizzle product's value
                    # since 'drizzle' itself doesn't update that keyword.
                    addWCSKeywords(self.wcs,hdu.header,blot=self.blot)

                # Add primary header to output file...
                fwht.append(hdu)
                fwht.writeto(self.outweight)
                del fwht,hdu

            # If a context image was specified, build a PyFITS object
            # for it as well...
            if self.outcontext and ctxarr != None:
                fctx = pyfits.HDUList()

                # If there is only 1 plane, write it out as a 2-D extension
                if ctxarr.shape[0] == 1:
                    _ctxarr = ctxarr[0]
                else:
                    _ctxarr = ctxarr

                hdu = pyfits.PrimaryHDU(data=_ctxarr, header=prihdu.header)

                # Append remaining unique header keywords from template DQ
                # header to Primary header...
                if dqhdr:
                    for _card in dqhdr.ascard:
                        if _card.key not in RESERVED_KEYS and hdu.header.has_key(_card.key) == 0:
                            hdu.header.ascard.append(_card)
                hdu.header.update('filename', self.outcontext)
                if self.wcs:
                    # Update WCS Keywords based on PyDrizzle product's value
                    # since 'drizzle' itself doesn't update that keyword.
                    addWCSKeywords(self.wcs,hdu.header,blot=self.blot)

                fctx.append(hdu)
                fctx.writeto(self.outcontext)
                del fctx,hdu

    def addDrizKeywords(self,hdr,versions):
        """ Add drizzle parameter keywords to header. """

        # Extract some global information for the keywords
        _geom = 'User parameters'

        _imgnum = 0
        for pl in self.parlist:

            # Start by building up the keyword prefix based
            # on the image number for the chip
            #_keyprefix = 'D%03d'%_imgnum
            _imgnum += 1

            drizdict = DRIZ_KEYWORDS.copy()
            # Update drizdict with current values
            drizdict['VER']['value'] = pl['driz_version'][:44]
            drizdict['DATA']['value'] = pl['data'][:64]
            drizdict['DEXP']['value'] = pl['exptime']
            drizdict['OUDA']['value'] = pl['outFinal'][:64]
            drizdict['OUWE']['value'] = pl['outWeight'][:64]
            if pl['outContext'] is None:
                outcontext = ""
            else:
                outcontext = pl['outContext'][:64]
            drizdict['OUCO']['value'] = outcontext
            if self.single:
                drizdict['MASK']['value'] = pl['singleDrizMask'][:64]
            else:
                drizdict['MASK']['value'] = pl['finalMask'][:64]

            # Process the values of WT_SCL to be consistent with
            # what IRAF Drizzle would output
            if pl['wt_scl'] == 'exptime': _wtscl = pl['exptime']
            elif pl['wt_scl'] == 'expsq': _wtscl = pl['exptime']*pl['exptime']
            else: _wtscl = pl['wt_scl']
            drizdict['WTSC']['value'] = _wtscl
            drizdict['KERN']['value'] = pl['kernel']
            drizdict['PIXF']['value'] = pl['pixfrac']
            drizdict['OUUN']['value'] = self.units
            if pl['fillval'] is None:
                _fillval = 'INDEF'
            else:
                _fillval = pl['fillval']
            drizdict['FVAL']['value'] = _fillval
            drizdict['WKEY']['value'] = pl['driz_wcskey']

            drizdict['SCAL'] = {'value':pl['scale'],'comment':'Drizzle, pixel size (arcsec) of output image'}
            drizdict['ISCL'] = {'value':pl['idcscale'],'comment':'Drizzle, default IDCTAB pixel size(arcsec)'}

            # Now update header with values
            writeDrizKeywords(hdr,_imgnum,drizdict)
            del drizdict

        # Add version information as HISTORY cards to the header
        if versions != None:
            ver_str = "PyDrizzle processing performed using: "
            hdr.add_history(ver_str)
            for k in versions.keys():
                ver_str = '    '+str(k)+' Version '+str(versions[k])
                hdr.add_history(ver_str)


def cleanTemplates(scihdr,errhdr,dqhdr):
    
    # Now, safeguard against having BSCALE and BZERO
    try:
        del scihdr['bscale']
        del scihdr['bzero']
        del errhdr['bscale']
        del errhdr['bzero']
        del dqhdr['bscale']
        del dqhdr['bzero']
    except:
        # If these don't work, they didn't exist to start with...
        pass

    # At this point, check errhdr and dqhdr to make sure they
    # have all the requisite keywords (as listed in updateDTHKeywords).
    # Simply copy them from scihdr if they don't exist...
    if errhdr != None and dqhdr != None:
        for keyword in WCS_KEYWORDS:
            if keyword not in errhdr:
                errhdr.update(keyword,scihdr[keyword])
            if keyword not in dqhdr:
                dqhdr.update(keyword,scihdr[keyword])

def getTemplates(fnames, blend=True):
    """ Process all headers to produce a set of combined headers
        that follows the rules defined by each instrument. 

    """
    if not blend:
        newhdrs =  blendheaders.getSingleTemplate(fnames[0])
        newtab = None
    else:
        # apply rules to create final version of headers, plus table
        newhdrs, newtab = blendheaders.get_blended_headers(inputs=fnames)  
    
    cleanTemplates(newhdrs[1],newhdrs[2],newhdrs[3])
    
    return newhdrs, newtab

def addWCSKeywords(wcs,hdr,blot=False):
    """ Update input header 'hdr' with WCS keywords.
    """
    # Update WCS Keywords based on PyDrizzle product's value
    # since 'drizzle' itself doesn't update that keyword.
    hdr.update('ORIENTAT',wcs.orientat)
    hdr.update('CD1_1',wcs.wcs.cd[0][0])
    hdr.update('CD1_2',wcs.wcs.cd[0][1])
    hdr.update('CD2_1',wcs.wcs.cd[1][0])
    hdr.update('CD2_2',wcs.wcs.cd[1][1])
    hdr.update('CRVAL1',wcs.wcs.crval[0])
    hdr.update('CRVAL2',wcs.wcs.crval[1])
    hdr.update('CRPIX1',wcs.wcs.crpix[0])
    hdr.update('CRPIX2',wcs.wcs.crpix[1])
    hdr.update('WCSNAME',wcs.wcs.name)
    hdr.update('VAFACTOR',1.0)
    if not hdr.has_key('ctype1'):
        hdr.update('CTYPE1',wcs.wcs.ctype[0])
        hdr.update('CTYPE2',wcs.wcs.ctype[1])

    if not blot:
        blendheaders.remove_distortion_keywords(hdr)

def writeSingleFITS(data,wcs,output,template,blot=False,clobber=True,verbose=True):
    """ Write out a simple FITS file given a numpy array and the name of another
    FITS file to use as a template for the output image header.
    """
    outname,outextn = fileutil.parseFilename(output)
    outextname,outextver = fileutil.parseExtn(outextn)

    if fileutil.findFile(outname):
        if clobber:
            log.info('Deleting previous output product: %s' % outname)
            fileutil.removeFile(outname)

        else:
            log.warning('Output file %s already exists and overwrite not '
                        'specified!' % outname)
            log.error('Quitting... Please remove before resuming operations.')
            raise IOError

    # Now update WCS keywords with values from provided WCS
    if hasattr(wcs.sip,'a_order'):
        siphdr = True
    else:
        siphdr = False
    wcshdr = wcs.wcs2header(sip2hdr=siphdr)

    if template is not None:
        # Get default headers from multi-extension FITS file
        # If input data is not in MEF FITS format, it will return 'None'
        # NOTE: These are HEADER objects, not HDUs
        (prihdr,scihdr,errhdr,dqhdr),newtab = getTemplates(template,EXTLIST)

        if scihdr is None:
            scihdr = pyfits.Header()
            indx = 0
            for c in prihdr.ascard:
                if c.key not in ['INHERIT','EXPNAME']: indx += 1
                else: break
            for i in range(indx,len(prihdr.ascard)):
                scihdr.ascard.append(prihdr.ascard[i])
            for i in range(indx,len(prihdr.ascard)):
                del prihdr.ascard[indx]
    else:
        scihdr = pyfits.Header()
        prihdr = pyfits.Header()
        # Start by updating PRIMARY header keywords...
        prihdr.update('EXTEND',pyfits.TRUE,after='NAXIS')
        prihdr.update('FILENAME', outname)

    for _card in wcshdr.ascard:
        scihdr.update(_card.key, _card.value, comment=_card.comment)

    if outextname == '':
        outextname = 'sci'
    if outextver == 0: outextver = 1
    scihdr.update('EXTNAME',outextname.upper())
    scihdr.update('EXTVER',outextver)

    for card in wcshdr.ascard:
        scihdr.update(card.key,card.value,comment=card.comment)

    # Create PyFITS HDUList for all extensions
    outhdu = pyfits.HDUList()
    # Setup primary header as an HDU ready for appending to output FITS file
    prihdu = pyfits.PrimaryHDU(header=prihdr)
    scihdu = pyfits.ImageHDU(header=scihdr,data=data)

    outhdu.append(prihdu)
    outhdu.append(scihdu)
    outhdu.writeto(outname)
    
    if verbose:
        print('Created output image: %s' % outname)


def writeDrizKeywords(hdr,imgnum,drizdict):
    """ Write basic drizzle-related keywords out to image header as a record
        of the processing performed to create the image

        The dictionary 'drizdict' will contain the keywords and values to be
        written out to the header.
    """
    _keyprefix = 'D%03d'%imgnum

    for key in drizdict:
        val = drizdict[key]['value']
        if val is None: val = ""
        comment = drizdict[key]['comment']
        if comment is None: comment = ""
        hdr.update(_keyprefix+key,val,comment=drizdict[key]['comment'])

