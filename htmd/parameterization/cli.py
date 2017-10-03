# (c) 2015-2017 Acellera Ltd http://www.acellera.com
# All Rights Reserved
# Distributed under HTMD Software License Agreement
# No redistribution in whole or part
#
import os
import sys
import argparse
import logging
import psutil
import numpy as np

from htmd.queues.localqueue import LocalCPUQueue
from htmd.queues.lsfqueue import LsfQueue
from htmd.queues.slurmqueue import SlurmQueue
from htmd.qm import Psi4, Gaussian, FakeQM
from htmd.parameterization.ffmolecule import FFMolecule
from htmd.parameterization.fftype import FFTypeMethod
from htmd.parameterization.ffevaluate import FFEvaluate

logger = logging.getLogger(__name__)


def getArgumentParser():

    parser = argparse.ArgumentParser(description="Acellera Small Molecule Parameterization Tool")
    parser.add_argument("-m", "--mol2", help="Molecule to parameterise, in mol2 format", required=True, type=str,
                        default=None, metavar="<input.mol2>", action="store", dest="mol")
    parser.add_argument("-l", "--list", "--list-torsions", help="List parameterisable torsions", action="store_true",
                        default=False,
                        dest="list")
    parser.add_argument("-c", "--charge", help="Net charge on molecule (default: sum of the partial charges on the "
                                               ".mol2 file)", type=int, default=None, action="store", dest="charge")
    parser.add_argument("--rtf", help="Inital RTF parameters (req --prm)", type=str, default=None, dest="rtf")
    parser.add_argument("--prm", help="Inital PRM parameters (req --rtf)", type=str, default=None, dest="prm")
    parser.add_argument("-o", "--outdir", help="Output directory (default: %(default)s)", type=str, default="./",
                        dest="outdir")
    parser.add_argument("-t", "--torsion", metavar="A1-A2-A3-A4", help="Torsion to parameterise (default: %(default)s)",
                        default="all", dest="torsion")
    parser.add_argument("-n", "--ncpus", help="Number of CPUs to use (default: %(default)s)", default=psutil.cpu_count(),
                        dest="ncpus")
    parser.add_argument("-f", "--forcefield", help="Inital FF guess to use (default: %(default)s)",
                        choices=["GAFF", "GAFF2", "CGENFF", "all"], default="all")
    parser.add_argument("-b", "--basis", help="QM Basis Set (default: %(default)s)", choices=["6-31G*", "cc-pVDZ"],
                        default="cc-pVDZ", dest="basis")
    parser.add_argument("--theory", help="QM Theory (default: %(default)s)", choices=["HF", "B3LYP"],
                        default="B3LYP", dest="theory")
    parser.add_argument("--vacuum", help="Perform QM calculations in vacuum (default: %(default)s)",
                        action="store_false", dest="vacuum",
                        default=True)
    parser.add_argument('--no-min', help='Do not perform QM minimisation (default: %(default)s)', action='store_false',
                        dest='minimize', default=True)
    parser.add_argument('--no-esp', help='Do not perform QM charge fitting (default: %(default)s)', action='store_false',
                        dest='fit_charges', default=True)
    parser.add_argument('--no-dihedrals', '--no-torsions', help='Do not perform dihedral angle fitting (default: %(default)s)',
                        action='store_false', dest='fit_dihedral', default=True)
    parser.add_argument("-e", "--exec", help="Mode of execution for the QM calculations (default: %(default)s)",
                        choices=["local", "LSF", "Slurm"], default="local", dest="exec")
    parser.add_argument("--qmcode", help="QM code (default: %(default)s)", choices=["Psi4", "Gaussian"],
                        default="Psi4", dest="qmcode")
    parser.add_argument("--freeze-charge", metavar="A1",
                        help="Freeze the charge of the named atom (default: %(default)s)", action="append",
                        default=None, dest="freezeq")
    parser.add_argument("--no-geomopt", help="Do not perform QM geometry optimisation when fitting torsions (default: "
                                             "%(default)s)", action="store_false", dest="geomopt", default=True)
    parser.add_argument("--seed", help="Random number generator seed (default: %(default)s)", type=int,
                        default=20170920, dest="seed")

    # Enable replacement of any real QM class with FakeQM.
    # This is intedended for debugging only and should be kept hidden.
    parser.add_argument("--fake-qm", help=argparse.SUPPRESS, action="store_true",dest="fake_qm", default=False)

    return parser


def printEnergies(molecule, filename):

    assert molecule.numFrames == 1
    energies = FFEvaluate(molecule).run(molecule.coords[:, :, 0])

    string = '''
== Diagnostic Energies ==

Bond     : {BOND_ENERGY}
Angle    : {ANGLE_ENERGY}
Dihedral : {DIHEDRAL_ENERGY}
Improper : {IMPROPER_ENERGY}
Electro  : {ELEC_ENERGY}
VdW      : {VDW_ENERGY}

'''.format(BOND_ENERGY=energies['bond'],
           ANGLE_ENERGY=energies['angle'],
           DIHEDRAL_ENERGY=energies['dihedral'],
           IMPROPER_ENERGY=energies['improper'],
           ELEC_ENERGY=energies['elec'],
           VDW_ENERGY=energies['vdw'])

    sys.stdout.write(string)
    with open(filename, 'w') as file_:
        file_.write(string)


def main_parameterize(arguments=None):

    args = getArgumentParser().parse_args(args=arguments)

    filename = args.mol
    if not os.path.exists(filename):
        raise ValueError('File %s cannot be found' % filename)

    # Create a queue for QM
    if args.exec == 'local':
        queue = LocalCPUQueue()
        queue.ncpu = args.ncpus
    elif args.exec == 'Slurm':
        queue = SlurmQueue()
        queue.partition = SlurmQueue._defaults['cpu_partition']
    elif args.exec == 'LSF':
        queue = LsfQueue()
        queue.queue = LsfQueue._defaults['cpu_queue']
    else:
        raise NotImplementedError

    # Create a QM object
    if args.qmcode == 'Psi4':
        qm = Psi4()
    elif args.qmcode == 'Gaussian':
        qm = Gaussian()
    else:
        raise NotImplementedError

    # This is for debugging only!
    if args.fake_qm:
        qm = FakeQM()
        logger.warning('Using FakeQM')

    # Set up the QM object
    qm.theory = args.theory
    qm.basis = args.basis
    qm.solvent = 'vacuum' if args.vacuum else 'PCM'
    qm.queue = queue

    if args.forcefield == 'CGENFF':
        methods = [FFTypeMethod.CGenFF_2b6]
    elif args.forcefield == 'GAFF':
        methods = [FFTypeMethod.GAFF]
    elif args.forcefield == 'GAFF2':
        methods = [FFTypeMethod.GAFF2]
    elif args.forcefield == 'all':
        methods = [FFTypeMethod.CGenFF_2b6, FFTypeMethod.GAFF2]
    else:
        raise NotImplementedError

    # List rotatable dihedral angles
    if args.list:
        print(" === Listing rotatable dihedral angles of {} ===\n".format(filename))
        mol = FFMolecule(filename=filename, method=methods[0], netcharge=args.charge, rtf=args.rtf, prm=args.prm,
                         qm=qm, outdir=args.outdir)
        print('Detected soft torsions:')
        with open('torsions.txt', 'w') as fh:
            for dihedral in mol.getRotatableDihedrals():
                name = '%s-%s-%s-%s' % tuple(mol.name[dihedral])
                print('\t'+name)
                fh.write(name+'\n')
        sys.exit(0)

    # Print arguments
    print('\n === Arguments ===\n')
    for key, value in vars(args).items():
        print('{:>12s}: {:s}'.format(key, str(value)))

    print('\n === Parameterizing %s ===\n' % filename)
    for method in methods:
        print(" === Fitting for %s ===\n" % method.name)

        mol = FFMolecule(filename=filename, method=method, netcharge=args.charge, rtf=args.rtf, prm=args.prm,
                         qm=qm, outdir=args.outdir)
        mol_orig = mol.copy()

        # Update B3LYP to B3LYP-D3
        # TODO: this is silent and not documented stuff
        if qm.theory == 'B3LYP':
            qm.correction = 'D3'

        # Update basis sets
        # TODO: this is silent and not documented stuff
        if mol.netcharge < 0 and qm.solvent == 'vacuum':
            if qm.basis == '6-31G*':
                qm.basis = '6-31+G*'
            if qm.basis == 'cc-pVDZ':
                qm.basis = 'aug-cc-pVDZ'
            logger.info('Changing basis sets to %s' % qm.basis)

        # Minimize molecule
        if args.minimize:
            print('\n == Minimizing ==\n')
            mol.minimize()

        # Fit ESP charges
        if args.fit_charges:
            print('\n == Fitting ESP charges ==\n')

            # Set random number generator seed
            if args.seed:
                np.random.seed(args.seed)

            # Select the atoms with fixed charges
            fixed_atom_indices = []
            if args.freezeq:

                for fixed_atom_name in args.freezeq:
                    if fixed_atom_name not in mol.name:
                        raise ValueError('Atom %s is not found. Check --freeze-charge arguments' % fixed_atom_name)

                    for aton_index in range(mol.numAtoms):
                        if mol.name[aton_index] == fixed_atom_name:
                            fixed_atom_indices.append(aton_index)
                            logger.info('Charge of atom %s is fixed to %f' % (fixed_atom_name, mol.charge[aton_index]))

            # Fit ESP charges
            score, qm_dipole = mol.fitCharges(fixed=fixed_atom_indices)

            # Print results
            mm_dipole = mol.getDipole()
            score = np.sum((qm_dipole[:3] - mm_dipole[:3])**2)
            print('Charge fitting score: %f\n' % score)
            print('QM dipole: %f %f %f; %f' % tuple(qm_dipole))
            print('MM dipole: %f %f %f; %f' % tuple(mm_dipole))
            print('Dipole Chi^2 score: %f\n' % score)

        # Fit dihedral angle parameters
        if args.fit_dihedral:
            print('\n == Fitting dihedral angle parameters ==\n')

            # Set random number generator seed
            if args.seed:
                np.random.seed(args.seed)

            # Get all rotatable dihedrals
            all_dihedrals = mol.getRotatableDihedrals()

            # Choose which dihedrals to fit
            dihedrals = []
            if args.torsion == 'all':
                dihedrals = all_dihedrals
            else:
                all_names = ['%s-%s-%s-%s' % tuple(mol.name[dihedral]) for dihedral in all_dihedrals]
                for name in args.torsion.split(','):
                    if name in all_names:
                        dihedrals.append(all_dihedrals[all_names.index(name)])
                    else:
                        raise ValueError('%s is not recognized as a rotatable dihedral angle\n' % name)

            # Fit the parameters
            mol.fitDihedrals(dihedrals, args.geomopt)

        # Output the FF parameters
        print('\n == Writing results ==\n')
        paramdir = os.path.join(args.outdir, 'parameters', method.name, mol.output_directory_name())
        os.makedirs(paramdir, exist_ok=True)

        if method.name == 'CGenFF_2b6':
            try:
                rftFile = os.path.join(paramdir, 'mol.rtf')
                mol._rtf.write(rftFile)  # TODO move to FFMolecule.write
                logger.info('Write RTF file: %s' % rftFile)

                prmFile = os.path.join(paramdir, 'mol.prm')
                mol._prm.write(prmFile)  # TODO move to FFMolecule.write
                logger.info('Write PRM file: %s' % prmFile)

                for ext in ('psf', 'xyz', 'coor', 'mol2', 'pdb'):
                    file_ = os.path.join(paramdir, "mol." + ext)
                    mol.write(file_)
                    logger.info('Write %s file: %s' % (ext.upper(), file_))

                molFile = os.path.join(paramdir, 'mol-orig.mol2')
                mol_orig.write(molFile)
                logger.info('Write MOL2 file (with original coordinates): %s' % molFile)

                # TODO: remove?
                f = open(os.path.join(paramdir, "input.namd"), "w")
                tmp = '''parameters mol.prm
paraTypeCharmm on
coordinates mol.pdb
bincoordinates mol.coor
temperature 0
timestep 0
1-4scaling 1.0
exclude scaled1-4
outputname .out
outputenergies 1
structure mol.psf
cutoff 20.
switching off
stepsPerCycle 1
rigidbonds none
cellBasisVector1 50. 0. 0.
cellBasisVector2 0. 50. 0.
cellBasisVector3 0. 0. 50.
run 0'''
                print(tmp, file=f)
                f.close()

            except ValueError as e:
                print("Not writing CHARMM PRM: {}".format(str(e)))

        elif method.name == 'GAFF' or method.name == 'GAFF2':
            try:
                # types need to be remapped because Amber FRCMOD format limits the type to characters
                # writeFrcmod does this on the fly and returns a mapping that needs to be applied to the mol
                # TODO: get rid of this mapping
                frcFile = os.path.join(paramdir, 'mol.frcmod')
                typemap = mol._prm.writeFrcmod(mol._rtf, frcFile)  # TODO move to FFMolecule.write
                logger.info('Write FRCMOD file: %s' % frcFile)

                for ext in ('coor', 'mol2', 'pdb'):
                    file_ = os.path.join(paramdir, "mol." + ext)
                    mol.write(file_, typemap=typemap)
                    logger.info('Write %s file: %s' % (ext.upper(), file_))

                molFile = os.path.join(paramdir, 'mol-orig.mol2')
                mol_orig.write(molFile, typemap=typemap)
                logger.info('Write MOL2 file (with original coordinates): %s' % molFile)

                tleapFile = os.path.join(paramdir, 'tleap.in')
                with open(tleapFile, 'w') as file_:
                    file_.write('loadAmberParams mol.frcmod\n')
                    file_.write('A = loadMol2 mol.mol2\n')
                    file_.write('saveAmberParm A structure.prmtop mol.crd\n')
                    file_.write('quit\n')
                logger.info('Write tleap input file: %s' % tleapFile)

                # TODO: remove?
                f = open(os.path.join(paramdir, "input.namd"), "w")
                tmp = '''parmfile structure.prmtop
amber on
coordinates mol.pdb
bincoordinates mol.coor
temperature 0
timestep 0
1-4scaling 0.83333333
exclude scaled1-4
outputname .out
outputenergies 1
cutoff 20.
switching off
stepsPerCycle 1
rigidbonds none
cellBasisVector1 50. 0. 0.
cellBasisVector2 0. 50. 0.
cellBasisVector3 0. 0. 50.
run 0'''
                print(tmp, file=f)
                f.close()

            except ValueError as e:
                print("Not writing Amber FRCMOD: {}".format(str(e)))

        else:
            raise NotImplementedError

        printEnergies(mol, 'energies.txt')

if __name__ == "__main__":

    main_parameterize(arguments=['-h'])
