from qiskit import QuantumRegister, ClassicalRegister, AncillaRegister, QuantumCircuit, qpy
from qiskit.quantum_info import Statevector, Operator
from qiskit_aer import AerSimulator
from qiskit_aer.noise import NoiseModel
from qiskit import transpile, assemble
from qiskit.visualization import plot_histogram, plot_bloch_multivector, plot_distribution, plot_state_qsphere
from qiskit_ibm_runtime import QiskitRuntimeService, Batch, Sampler
from qiskit.result.utils import marginal_distribution
from qiskit_ibm_runtime.fake_provider import FakeMumbai
from qiskit.quantum_info import hellinger_fidelity

import matplotlib.pyplot as plt
import numpy as np
import math
import logging
import time
import statistics
import os
import pickle
import sys

import qubit_lattice
import phase
import frqi
import btq_plotter
import supermarq_metrics
import traceback

# setup logging
os.makedirs("./experiment_data", exist_ok=True)

import logging.config
logging.config.dictConfig({
    'version': 1,
    # Other configs ...
    'disable_existing_loggers': True
})

logging.basicConfig(level=logging.DEBUG, filename=os.path.join("experiment_data", f"btq_{time.strftime('%Y-%m-%d')}.log"), filemode="w", format='%(asctime)s - %(levelname)s - (%(funcName)s) = %(message)s')
logger = logging.getLogger("btq_logs")

# logging.getLogger('stevedore.extension').setLevel(logging.CRITICAL)
# logging.getLogger('qiskit.passmanager.base_tasks').setLevel(logging.CRITICAL)
# logging.getLogger('qiskit.transpiler.passes.basis.basis_translator').setLevel(logging.CRITICAL)
# logging.getLogger('qiskit.compiler.transpiler').setLevel(logging.CRITICAL)
# logging.getLogger('stevedore._cache').setLevel(logging.CRITICAL)
# logging.getLogger('qiskit.compiler.assembler').setLevel(logging.CRITICAL)


# Qiskit backend basics
qiskitService = None
pure_backend = AerSimulator()
noisy_backend = AerSimulator()
ibmq_backend = None

#___________________________________
# Qiskit Backends
''' Get IBMQ token from extern file'''
def getIBMQtoken():
    try:
        with open('ibmq.token', 'r') as f:
            ibmqt = str(f.read())
        return ibmqt
    
    except Exception as e:
        print("ibmq.token file not found. Aborting.")
        exit()

def getIBMQService():
    return QiskitRuntimeService(channel="ibm_quantum", token=getIBMQtoken())

''' Noisy backend'''
def setupNoisyBackend():
    qiskitService = QiskitRuntimeService(channel="ibm_quantum", token=getIBMQtoken())

    ''' Noisy model from AER: https://qiskit.github.io/qiskit-aer/stubs/qiskit_aer.noise.NoiseModel.html '''
    # Get a fake backend from the fake provider
    # backend = FakeMumbai()
    # noise_model = NoiseModel.from_backend(backend)

    # # Get coupling map from backend
    # coupling_map = backend.configuration().coupling_map

    # # Get basis gates from noise model
    # basis_gates = noise_model.basis_gates

    # noisy_backend = AerSimulator(noise_model=noise_model,
    #                        coupling_map=coupling_map,
    #                        basis_gates=basis_gates)

    ''' Noisy model from QiskitRuntimeService: https://docs.quantum.ibm.com/api/qiskit-ibm-runtime/dev/fake_provider '''
    noisy_backend = qiskitService.get_backend('ibm_kyoto')
    noisy_backend = AerSimulator.from_backend(noisy_backend)
    return noisy_backend

''' IBMQ Hardware '''
def setupIBMQBackend():
    qiskitService = QiskitRuntimeService(channel="ibm_quantum", token=getIBMQtoken())
    
    # To run on hardware, select the backend with the fewest number of jobs in the queue
    return qiskitService.least_busy(operational=True, simulator=False)

#___________________________________
# INPUT
def prepareInput(n=4, input_range=(0, 255), angle_range=(0, np.pi/2), dist="linear", verbose=1):
    side = int(math.sqrt(n))
    if dist.lower() == "random":
        input_vector = np.random.uniform(low=0, high=255, size=n, dtype=int)
    
    elif dist.lower() == "reversing":
        input_vector = []
        init_vector = np.linspace(start=0, stop=255, num=n, dtype=int)

        for i in range(side):
            input_vector.extend(init_vector[i*side:i*side+side] if not i%2 else init_vector[i*side+side-1:i*side-1:-1])
    else:
        input_vector = np.linspace(start=0, stop=255, num=n, dtype=int)

    input_angles = np.interp(input_vector, input_range, angle_range) 
    
    if verbose: logger.debug(f'Inputs: size({n}), Vector: {input_vector}, Angles: {input_angles}')
    
    return input_vector, input_angles

#___________________________________
# TRANSPILE CIRCUIT
def transpileCircuit(qc: QuantumCircuit, noisy=False, backend="simulator"):
    # return transpile(qc, backend=ibmq_backend, optimization_level=0, seed_transpiler=0,  basis_gates=['u', 'cx'])
    if backend == "simulator":
        if noisy:
            return transpile(qc, pure_backend, basis_gates=['u', 'cx'])
        else:
            return transpile(qc, noisy_backend, basis_gates=['u', 'cx'])
    
    elif backend == "ibmq":
        return transpile(qc, backend=ibmq_backend, optimization_level=1, seed_transpiler=0)

#___________________________________
# SIMULATE CIRCUIT
def simulate(tqc: QuantumCircuit, shots: int, noisy=False, verbose=1, backend="simulator"):
    
    if backend == "simulator":
        if noisy:
            job = noisy_backend.run(tqc, shots=shots)
        else:    
            job = pure_backend.run(tqc, shots=shots)

            result = job.result()
        
        if verbose: logger.debug(result.get_counts())
    
        return result

    # https://learning.quantum.ibm.com/tutorial/submit-transpiled-circuits#step-3-execute-using-qiskit-primitives
    # https://docs.quantum.ibm.com/api/migration-guides/v2-primitives#steps-to-migrate-to-sampler-v2
    elif backend == "ibmq":
        input("Waiting to submit to IBMQ")
        job = ibmq_backend.run(circuits=tqc, shots=shots)
        # with Batch(service=qiskitService, backend=ibmq_backend):
        #     sampler = Sampler()
        #     job = sampler.run(
        #         circuits=tqc,
        #         skip_transpilation=True,
        #         shots=10000,
        #     )
        return job


#___________________________________
# SIMULATE CIRCUIT
def get_ibmq_results(jobs, verbose=0):
    for job in jobs:
        result = job.result()

        if verbose: print(result)

        with open(os.path.join("experiment_data", f"exp_{time.strftime('%Y-%m-%d')}_{ibmq_backend.name}_{job.job_id()}.pkl"), 'wb') as f:
            pickle.dump(result, f)

#___________________________________
# SIMULATE CIRCUIT
def simulate_stateVec(qc: QuantumCircuit, verbose=1):
    job = noisy_backend.run(qc, shots=shots)

    result = job.result()
    counts = result.get_counts()

    if verbose:
        logger.debug(counts)
        # display(plot_histogram(counts))
    
    return counts

#___________________________________
# Calulate hellinger_fidelity
def calculate_fidelity(output_distribution, stateVector):
    return hellinger_fidelity(output_distribution, stateVector.probabilities_dict())

#___________________________________
# QUBIT LATTICE EXPERIMENT
def qubitLatticeExperiment(n=4, shots=1000000, verbose=0, run_simulation=False, exp_dict=None, noisy=False, dist="linear"):
    """Run the qubit lattice experiment and collect metrics.

    Args:
        n (int, optional): input size. Defaults to 4.
        shots (int, optional): number of shots. Defaults to 1000000.
        verbose (int, optional): level of logs. Defaults to 0.
        run_simulation (bool, optional): Run the simulation or just transpile and store the circuit. Defaults to False.
        exp_dict (_type_, optional): experiment_dict from btq_plotter to store the metrics in. Defaults to None.
        noisy (bool, optional): Run pure or noisy simulation. Defaults to False.
        dist (str, optional): type of input distribution. Refer to the default before main function. Defaults to "linear".

    Returns:
        exp_dict, circuit, accuracy 
    """
    logger.debug(f'> Qubit Lattice Experiment:: Image size: {math.sqrt(n)} x {math.sqrt(n)}\tShots: {shots} (noisy={noisy})')

    init_time = time.process_time()

    # input
    input_vector, input_angles = prepareInput(n=n, input_range=(0, 255), angle_range=(0, np.pi), verbose=verbose, dist=dist)
    circuit = QuantumCircuit()

    #---------------------

    # encoding
    qubit_lattice.qubitLatticeEncoder(qc=circuit, angles=input_angles, verbose=verbose)
    end_time = time.process_time() - init_time
    
    if exp_dict:
        if noisy: exp_dict["runtimes"]["Noisy Encoder"].append(end_time)
        else: 
            exp_dict["runtimes"]["Encoder"].append(time.process_time())
            exp_dict["depths"]["Encoder"].append(circuit.depth())
            exp_dict["widths"].append(circuit.num_qubits)
    
    logger.info(f'{{"Profiler":"Encoder", "runtime":"{end_time}", "depth":"{circuit.depth()}", "width":"{circuit.num_qubits}", "Exp":"Qubit Lattice,{n},{shots}"}}')
    
    #---------------------

    # invert + measurements
    qubit_lattice.invertPixels(qc=circuit, verbose=verbose)
    stateVector = Statevector(circuit)
    qubit_lattice.addMeasurements(qc=circuit, verbose=verbose)
    
    logger.info(f'{{"Profiler":"Invert + Measurement", "depth":"{circuit.depth()}", "width":"{circuit.num_qubits}", "Exp":"Qubit Lattice,{n},{shots}"}}')
    
    if exp_dict and not noisy:
        exp_dict["depths"]["Invert + Measurement"].append(circuit.depth())
    
    if verbose: logger.debug(f'Total Circuit depth: {circuit.depth}\tCircuit Width: {circuit.num_qubits}')

    #---------------------

    # transpile
    init_time = time.process_time()
    tcircuit = transpileCircuit(qc=circuit, noisy=noisy)
    end_time = time.process_time() - init_time

    if exp_dict:
        if noisy: exp_dict["runtimes"]["Noisy Transpile"].append(end_time)
        else: 
            exp_dict["runtimes"]["Transpile"].append(end_time)
            exp_dict["depths"]["Transpile"].append(tcircuit.depth())
            exp_dict["count_ops"].append(tcircuit.count_ops())
    logger.info(f'{{"Profiler":"Transpile", "runtime":"{end_time}", "depth":"{tcircuit.depth()}", "width":"{tcircuit.num_qubits}", "count_ops":"{tcircuit.count_ops()}", "Exp":"Qubit Lattice,{n},{shots}"}}')
    
    #---------------------

    # run experiment
    if run_simulation:

        # load transpiled circuit
        # with open(os.path.join('experiment_data', f'ql_{n}x{n}_{circuit.num_qubits}.qpy'), 'rb') as f:
        #     stored_tcircuit = qpy.load(f)[0]
        
        # simulate
        result_obj = simulate(tqc=tcircuit, shots=shots, verbose=verbose)
        simulation_time = result_obj.time_taken
        experiment_result_counts = result_obj.get_counts()

        logger.info(f'{{"Profiler":"Simulate", "runtime":"{simulation_time}", "depth":"{tcircuit.depth()}", "width":"{tcircuit.num_qubits}", "Exp":"Qubit Lattice,{n},{shots}"}}')
        if exp_dict:
            if noisy: exp_dict["runtimes"]["Noisy Simulate"].append(simulation_time)
            else: 
                exp_dict["runtimes"]["Simulate"].append(simulation_time)
                exp_dict["depths"]["Simulate"].append(circuit.depth())

    #---------------------

        # fidelity
        # if exp_dict and not noisy:
        #     big_endian_counts = {}
            
        #     for key, value in experiment_result_counts.items():
        #         big_endian_counts[key[::-1]] = value
            
        #     exp_dict['fidelities'].append(calculate_fidelity(big_endian_counts, stateVector))
    
    #---------------------

        # decode
        init_time = time.process_time()  
        output_vector = qubit_lattice.qubitLatticeDecoder(counts=experiment_result_counts, n=n, shots=shots)
        end_time = time.process_time() - init_time
        
        logger.info(f'{{"Profiler":"Decoder", "runtime":"{end_time}", "Exp":"Qubit Lattice,{n},{shots}"}}')
        if exp_dict:
            if noisy: exp_dict["runtimes"]["Noisy Decoder"].append(end_time)
            else: exp_dict["runtimes"]["Decoder"].append(end_time)

    #---------------------

        # data points
        logger.info(f'{{"Profiler":"Data Points", "original_values": {list(input_vector)}, "reconstructed_values": {output_vector}}}')
        if exp_dict:
            if noisy: exp_dict['noisy_data_points'].append([list(input_vector), list(output_vector)])
            else: exp_dict['data_points'].append([list(input_vector), list(output_vector)])

    #---------------------

        # accuracy
        accuracy = statistics.fmean([1 - round(abs(output_vector[i] - (255 - input_vector[i]))/max((255 - input_vector[i]), output_vector[i]),4) if (255-input_vector[i]) != output_vector[i] else 1 for i in range(n)])
        logger.info(f'{{"Profiler":"Accuracy", "value":"{accuracy}", "Exp":"Qubit Lattice,{n},{shots}"}}')

        if exp_dict:
            if noisy: exp_dict['noisy_accuracy'].append(accuracy)
            else: exp_dict['accuracy'].append(accuracy)
    
    else:
        # store transpiled circuit
        with open(os.path.join('experiment_data', f'ql_{n}x{n}_{circuit.num_qubits}.qpy'), 'wb') as f:
            qpy.dump(tcircuit, f)

    return exp_dict, tcircuit, accuracy

#___________________________________
# PHASE EXPERIMENT
def phaseExperiment(n=4, shots=1000000, verbose=0, run_simulation=False, exp_dict=None, noisy=False, dist="linear"):
    """Run thephase encoding experiment and collect metrics.

    Args:
        n (int, optional): input size. Defaults to 4.
        shots (int, optional): number of shots. Defaults to 1000000.
        verbose (int, optional): level of logs. Defaults to 0.
        run_simulation (bool, optional): Run the simulation or just transpile and store the circuit. Defaults to False.
        exp_dict (_type_, optional): experiment_dict from btq_plotter to store the metrics in. Defaults to None.
        noisy (bool, optional): Run pure or noisy simulation. Defaults to False.
        dist (str, optional): type of input distribution. Refer to the default before main function. Defaults to "linear".

    Returns:
        exp_dict, circuit, accuracy 
    """
    logger.debug(f'> Phase Encoding Experiment:: Image size: {math.sqrt(n)} x {math.sqrt(n)}\tShots: {shots} (noisy={noisy})')
    
    init_time = time.process_time()

    # input
    input_vector, input_angles = prepareInput(n=n, input_range=(0, 255), angle_range=(0, np.pi), verbose=verbose, dist=dist)

    circuit = QuantumCircuit()

    #---------------------

    # encoding
    phase.phaseEncoder(qc=circuit, angles=input_angles, verbose=verbose)
    end_time = time.process_time() - init_time
    
    if exp_dict:
        if noisy: exp_dict["runtimes"]["Noisy Encoder"].append(end_time)
        else: 
            exp_dict["runtimes"]["Encoder"].append(time.process_time())
            exp_dict["depths"]["Encoder"].append(circuit.depth())
            exp_dict["widths"].append(circuit.num_qubits)
    logger.info(f'{{"Profiler":"Encoder", "runtime":"{time.process_time() - init_time}", "depth":"{circuit.depth()}", "width":"{circuit.num_qubits}", "Exp":"Phase,{n},{shots}"}}')

    #---------------------

    # invert + measurements
    phase.invertPixels(qc=circuit, verbose=verbose)
    stateVector = Statevector(circuit)
    phase.addMeasurements(qc=circuit, verbose=verbose)
    
    logger.info(f'{{"Profiler":"Invert + Measurement", "depth":"{circuit.depth()}", "width":"{circuit.num_qubits}", "Exp":"Phase,{n},{shots}"}}')
    if exp_dict and not noisy:
        exp_dict["depths"]["Invert + Measurement"].append(circuit.depth())
    
    if verbose: logger.debug(f'Total Circuit depth: {circuit.depth}\tCircuit Width: {circuit.num_qubits}')

    #---------------------

    # transpile
    init_time = time.process_time()
    tcircuit = transpileCircuit(qc=circuit, noisy=noisy)
    end_time = time.process_time() - init_time

    if exp_dict:
        if noisy: exp_dict["runtimes"]["Noisy Transpile"].append(end_time)
        else: 
            exp_dict["runtimes"]["Transpile"].append(end_time)
            exp_dict["depths"]["Transpile"].append(tcircuit.depth())
            exp_dict["count_ops"].append(tcircuit.count_ops())
    logger.info(f'{{"Profiler":"Transpile", "runtime":"{end_time}", "depth":"{tcircuit.depth()}", "width":"{tcircuit.num_qubits}", "count_ops":"{tcircuit.count_ops()}", "Exp":"Phase,{n},{shots}"}}')

    #---------------------
    
    # run experiment
    if run_simulation:

        # load transpiled circuit
        # with open(os.path.join('experiment_data', f'ql_{n}x{n}_{circuit.num_qubits}.qpy'), 'rb') as f:
        #     stored_tcircuit = qpy.load(f)[0]
        
        # simulate
        result_obj = simulate(tqc=tcircuit, shots=shots, verbose=verbose)
        simulation_time = result_obj.time_taken
        experiment_result_counts = result_obj.get_counts()

        logger.info(f'{{"Profiler":"Simulate", "runtime":"{simulation_time}", "depth":"{tcircuit.depth()}", "width":"{tcircuit.num_qubits}", "Exp":"Phase,{n},{shots}"}}')
        if exp_dict:
            if noisy: exp_dict["runtimes"]["Noisy Simulate"].append(simulation_time)
            else: 
                exp_dict["runtimes"]["Simulate"].append(simulation_time)
                exp_dict["depths"]["Simulate"].append(circuit.depth())

    #---------------------

        # fidelity
        # if exp_dict and not noisy:
        #     exp_dict['fidelities'].append(calculate_fidelity(experiment_result_counts, stateVector))

    #---------------------

        # decode
        init_time = time.process_time()  
        output_vector = phase.phaseDecoder(counts=experiment_result_counts, n=n, shots=shots)
        end_time = time.process_time() - init_time
        
        logger.info(f'{{"Profiler":"Decoder", "runtime":"{end_time}", "Exp":"Phase,{n},{shots}"}}')
        if exp_dict:
            if noisy: exp_dict["runtimes"]["Noisy Decoder"].append(end_time)
            else: exp_dict["runtimes"]["Decoder"].append(end_time)
        
    #---------------------

        # data points
        logger.info(f'{{"Profiler":"Data Points", "original_values": {list(input_vector)}, "reconstructed_values": {output_vector}}}')
        if exp_dict:
            if noisy: exp_dict['noisy_data_points'].append([list(input_vector), list(output_vector)])
            else: exp_dict['data_points'].append([list(input_vector), list(output_vector)])

    #---------------------

        # accuracy
        accuracy = statistics.fmean([1 - round(abs(output_vector[i] - (255 - input_vector[i]))/max((255 - input_vector[i]), output_vector[i]),4) if (255-input_vector[i]) != output_vector[i] else 1 for i in range(n)])
        logger.info(f'{{"Profiler":"Accuracy", "value":"{accuracy}", "Exp":"Phase,{n},{shots}"}}')

        if exp_dict:
            if noisy: exp_dict['noisy_accuracy'].append(accuracy)
            else: exp_dict['accuracy'].append(accuracy)
    
    else:
        # store transpiled circuit
        with open(os.path.join('experiment_data', f'ql_{n}x{n}_{circuit.num_qubits}.qpy'), 'wb') as f:
            qpy.dump(tcircuit, f)
    
    return exp_dict, tcircuit, accuracy

#___________________________________
# FRQI EXPERIMENT
def frqiExperiment(n=4, shots=1000000, verbose=0, run_simulation=False, exp_dict=None, noisy=False, dist="linear", backend="simulator"):
    """Run the FRQI experiment and collect metrics.

    Args:
        n (int, optional): input size. Defaults to 4.
        shots (int, optional): number of shots. Defaults to 1000000.
        verbose (int, optional): level of logs. Defaults to 0.
        run_simulation (bool, optional): Run the simulation or just transpile and store the circuit. Defaults to False.
        exp_dict (_type_, optional): experiment_dict from btq_plotter to store the metrics in. Defaults to None.
        noisy (bool, optional): Run pure or noisy simulation. Defaults to False.
        dist (str, optional): type of input distribution. Refer to the default before main function. Defaults to "linear".
        backend (str, optional): Simulator or IBMQ. Defaults to "simulator".

    Returns:
        exp_dict, circuit, accuracy 
    """
    logger.debug(f'> FRQI Experiment:: Image size: {math.sqrt(n)} x {math.sqrt(n)}\tShots: {shots} (noisy={noisy}, backend={backend})')

    init_time = time.process_time()

    # input
    input_vector, input_angles = prepareInput(n=n, input_range=(0, 255), angle_range=(0, np.pi/2), dist=dist, verbose=verbose)
    circuit = QuantumCircuit()

    #---------------------

    # encode
    frqi.frqiEncoder(qc=circuit, angles=input_angles, verbose=verbose)
    end_time = time.process_time() - init_time
    
    if exp_dict:
        if noisy: exp_dict["runtimes"]["Noisy Encoder"].append(end_time)
        else: 
            exp_dict["runtimes"]["Encoder"].append(end_time)
            exp_dict["depths"]["Encoder"].append(circuit.depth())
            exp_dict["widths"].append(circuit.num_qubits)
    
    logger.info(f'{{"Profiler":"Encoder", "runtime":"{end_time}", "depth":"{circuit.depth()}", "width":"{circuit.num_qubits}", "Exp":"FRQI,{n},{shots}"}}')

    #---------------------

    # invert + measurements
    frqi.invertPixels(qc=circuit, verbose=verbose)
    stateVector = Statevector(circuit)
    frqi.addMeasurements(qc=circuit, verbose=verbose)
    
    logger.info(f'{{"Profiler":"Invert + Measurement", "depth":"{circuit.depth()}", "width":"{circuit.num_qubits}", "Exp":"FRQI,{n},{shots}"}}')
    if exp_dict and not noisy:
        exp_dict["depths"]["Invert + Measurement"].append(circuit.depth())

    if verbose: logger.debug(f'Circuit depth: {circuit.depth()}\tCircuit Width: {circuit.num_qubits}')
    #---------------------

    # transpile
    init_time = time.process_time()
    tcircuit = transpileCircuit(qc=circuit, noisy=noisy, backend=backend)
    end_time = time.process_time() - init_time
    
    if exp_dict:
        if noisy: exp_dict["runtimes"]["Noisy Transpile"].append(end_time)
        else: 
            exp_dict["runtimes"]["Transpile"].append(end_time)
            exp_dict["depths"]["Transpile"].append(tcircuit.depth())
            exp_dict["count_ops"].append(tcircuit.count_ops())
    logger.info(f'{{"Profiler":"Transpile", "runtime":"{end_time}", "depth":"{tcircuit.depth()}", "width":"{tcircuit.num_qubits}", "count_ops":"{tcircuit.count_ops()}", "Exp":"FRQI,{n},{shots}"}}')

    #---------------------
    
    # run experiment
    if run_simulation:

        # load transpiled circuit
        # with open(os.path.join('experiment_data', f'frqi_{n}x{n}_{circuit.num_qubits}.qpy'), 'rb') as f:
        #     stored_tcircuit = qpy.load(f)[0]

        # simulate
        result_obj = simulate(tqc=tcircuit, shots=shots, verbose=verbose)
        simulation_time = result_obj.time_taken
        experiment_result_counts = result_obj.get_counts()
        
        logger.info(f'{{"Profiler":"Simulate", "runtime":"{simulation_time}", "depth":"{tcircuit.depth()}", "width":"{tcircuit.num_qubits}", "Exp":"FRQI,{n},{shots}"}}')
        
        if exp_dict:
            if noisy: exp_dict["runtimes"]["Noisy Simulate"].append(simulation_time)
            else: 
                exp_dict["runtimes"]["Simulate"].append(simulation_time)
                exp_dict["depths"]["Simulate"].append(circuit.depth())
        
    #---------------------

        # fidelity        
        # if exp_dict and not noisy:
        #     exp_dict['fidelities'].append(calculate_fidelity(experiment_result_counts, stateVector))

    #---------------------

        # decode
        init_time = time.process_time()
        output_vector = frqi.frqiDecoder(counts=experiment_result_counts, n=n)
        end_time = time.process_time() - init_time
        
        logger.info(f'{{"Profiler":"Decoder", "runtime":"{end_time}", "Exp":"FRQI,{n},{shots}"}}')
        if exp_dict:
            if noisy: exp_dict["runtimes"]["Noisy Decoder"].append(end_time)
            else: exp_dict["runtimes"]["Decoder"].append(end_time)
    
    #---------------------

        # data points
        logger.info(f'{{"Profiler":"Data Points", "original_values": {list(input_vector)}, "reconstructed_values": {output_vector}}}')
        if exp_dict:
            if noisy: exp_dict['noisy_data_points'].append([list(input_vector), list(output_vector)])
            else: exp_dict['data_points'].append([list(input_vector), list(output_vector)])

    #---------------------

        # accuracy
        accuracy = statistics.fmean([1 - round(abs(output_vector[i] - (255 - input_vector[i]))/max((255 - input_vector[i]), output_vector[i]),4) if (255-input_vector[i]) != output_vector[i] else 1 for i in range(n)])
        logger.info(f'{{"Profiler":"Accuracy", "value":"{accuracy}", "Exp":"FRQI,{n},{shots}"}}')

        if exp_dict:
            if noisy: exp_dict['noisy_accuracy'].append(accuracy)
            else: exp_dict['accuracy'].append(accuracy)
        
    else:            
        # store transpiled circuit
        with open(os.path.join('experiment_data', f'frqi_{n}x{n}_{circuit.num_qubits}.qpy'), 'wb') as f:
            qpy.dump(tcircuit, f)

    return exp_dict, tcircuit, accuracy

#___________________________________
# FRQI EXPERIMENT IBMQ
def frqiExperimentIBMQ(n=4, shots:int=10000, verbose=0, mode="Submit", exp_dict=None, dist="linear"):
    """_summary_

    Args:
        n (int, optional): _description_. Defaults to 4.
        shots (int, optional): _description_. Defaults to 1000000.
        verbose (int, optional): _description_. Defaults to 0.
        run_simulation (bool, optional): _description_. Defaults to False.
        exp_dict (_type_, optional): _description_. Defaults to None.
        noisy (bool, optional): _description_. Defaults to False.
        dist (str, optional): _description_. Defaults to "linear".
        backend (str, optional): _description_. Defaults to "simulator".

    Returns:
        _type_: _description_
    """
    logger.debug(f'> FRQI Experiment IBMQ:: Image size: {math.sqrt(n)} x {math.sqrt(n)}\tShots: {shots} (mode: {mode} = {mode == "decode"})')

    if mode == "submit":

        init_time = time.process_time()
        # input
        input_vector, input_angles = prepareInput(n=n, input_range=(0, 255), angle_range=(0, np.pi/2), dist=dist, verbose=verbose)

        # citcuit
        circuit = QuantumCircuit()

        #---------------------

        # encode
        frqi.frqiEncoder(qc=circuit, angles=input_angles, verbose=verbose)
        end_time = time.process_time() - init_time
            
        exp_dict["runtimes"]["Encoder"].append(end_time)
        exp_dict["depths"]["Encoder"].append(circuit.depth())
        exp_dict["widths"].append(circuit.num_qubits)
        logger.info(f'{{"Profiler":"Encoder", "runtime":"{end_time}", "depth":"{circuit.depth()}", "width":"{circuit.num_qubits}", "Exp":"FRQI,{n},{shots}"}}')

        #---------------------

        # invert + measurements
        frqi.invertPixels(qc=circuit, verbose=verbose)

        stateVector = Statevector(circuit)
        exp_dict['stateVectors'].append(stateVector)

        frqi.addMeasurements(qc=circuit, verbose=verbose)

        logger.info(f'{{"Profiler":"Invert + Measurement", "depth":"{circuit.depth()}", "width":"{circuit.num_qubits}", "Exp":"FRQI,{n},{shots}"}}') 
        exp_dict["depths"]["Invert + Measurement"].append(circuit.depth())

        if verbose: logger.debug(f'Circuit depth: {circuit.depth()}\tCircuit Width: {circuit.num_qubits}')

        #---------------------

        # transpile
        init_time = time.process_time()
        tcircuit = transpileCircuit(qc=circuit, backend="ibmq")
        
        # with open(os.path.join('experiment_data', f'frqi_ibmq_{n}x{n}.qpy'), 'rb') as f:
        #     tcircuit = qpy.load(f)[0]
        
        end_time = time.process_time() - init_time
        
        exp_dict["runtimes"]["Transpile"].append(end_time)
        exp_dict["depths"]["Transpile"].append(circuit.depth())
        logger.info(f'{{"Profiler":"Transpile", "runtime":"{end_time}", "depth":"{tcircuit.depth()}", "width":"{tcircuit.num_qubits}", "count_ops":"{tcircuit.count_ops()}", "Exp":"FRQI,{n},{shots}"}}')

        exp_dict['count_ops'].append(tcircuit.count_ops())
        
        # store transpiled circuit
        # with open(os.path.join('experiment_data', f'frqi_ibmq_{n}x{n}.qpy'), 'wb') as f:
        #     qpy.dump(tcircuit, f)
        
        #---------------------

        # simulate
        init_time = time.process_time()
        job = simulate(tqc=tcircuit, shots=shots, verbose=verbose, backend="ibmq")

        logger.info(f'{{"Profiler":"Simulate", "runtime":"{time.process_time() - init_time}", "depth":"{tcircuit.depth()}", "width":"{tcircuit.num_qubits}", "Exp":"FRQI,{n},{shots}"}}')
        exp_dict["runtimes"]["Simulate"].append(time.process_time() - init_time)
        exp_dict['jobs'].append(job)

        return exp_dict

    elif mode == "decode":

        try:
            if not exp_dict['results']:
                for job in exp_dict['jobs']:
                    retrieved_job = qiskitService.job(job)
                    result = retrieved_job.result()    
                    exp_dict['results'].append(result)

        except Exception as e:
            print({traceback.format_exc()})

        #---------------------
        for i, result in enumerate(exp_dict['results']):
            input_vector, input_angles = prepareInput(n=exp_dict['size'][i], input_range=(0, 255), angle_range=(0, np.pi/2), dist=dist, verbose=verbose)
            # decode
            experiment_result_counts = result.get_counts()

            exp_dict["runtimes"]["Simulate"].append(result.time_taken)


            init_time = time.process_time()
            output_vector = frqi.frqiDecoder(counts=experiment_result_counts, n=exp_dict['size'][i])
            end_time = time.process_time() - init_time
            
            logger.info(f'{{"Profiler":"Decoder", "runtime":"{end_time}", "Exp":"FRQI,{exp_dict["size"][i]},{shots}"}}')
            if exp_dict:
                exp_dict["runtimes"]["Decoder"].append(end_time)
            
        #---------------------

            # data points
            logger.info(f'{{"Profiler":"Data Points", "original_values": {list(input_vector)}, "reconstructed_values": {output_vector}}}')
            if exp_dict:
                exp_dict['data_points'].append([list(input_vector), list(output_vector)])

        #---------------------

            # accuracy
            accuracy = statistics.fmean([1 - round(abs(output_vector[i] - (255 - input_vector[i]))/max((255 - input_vector[i]), output_vector[i]),4) if (255-input_vector[i]) != output_vector[i] else 1 for i in range(exp_dict['size'][i])])
            logger.info(f'{{"Profiler":"Accuracy", "value":"{accuracy}", "Exp":"FRQI,{exp_dict["size"][i]},{shots}"}}')

            exp_dict['accuracy'].append(accuracy)
        
        return exp_dict

#___________________________________
# DEFAULTS:
# Input runs
power_inputs = lambda n: [(2**x)**2 for x in range(1, n+1)]
square_inputs = lambda n: [x**2 for x in range(2, n+1)]

# Shots
shots = 50000

# input distribution ["reversing", "random", "linear"]
dist = "reversing"

# experiments: ["all", "ql", "phase", "frqi", "ibmq", "shots", "backends"]
experiments = "all"

# THE MAIN
#___________________________________
if __name__ == "__main__":
    
    # cmd arguments
    if len(sys.argv) > 1 and sys.argv[1] in ["all", "ql", "ph", "frqi", "ibmq", "shots"]: 
        experiments = sys.argv[1]

    if len(sys.argv) > 2: 
        shots = sys.argv[2]

    if len(sys.argv) > 3 and sys.argv[3] in ["reversing", "random", "linear"]: 
        dist = sys.argv[3]

    if experiments in ["all", "ql", "ph", "frqi", "ibmq"]: 
        # noisy_backend = setupNoisyBackend()
        ibmq_backend = setupIBMQBackend()

        ql_ph_inputs = square_inputs(5)
        frqi_inputs = power_inputs(4)
        exp_list = []

    print(f"\n- BTQ - Trial runs\t[{shots if experiments != 'shots' else '[5000, ..., 100000]'} shots - {dist} input - {experiments} experiments]\n")

    # input("Human Intervention requested")

    if experiments in ["all", "ql"]:
        #----------------------------------
        print(f"Qubit Lattice Experiments")

        exp = btq_plotter.get_dict("exp")
        exp['name'] = "Qubit Lattice"

        for i, input in enumerate(ql_ph_inputs):        
            print("\033[K", f"\t{i+1}/{len(ql_ph_inputs)} - {input}", end='\r')

            try:
                exp['shots'].append(shots)
                exp['size'].append(input)

                # Pure
                init_time = time.process_time()
                exp, circuit, accuracy = qubitLatticeExperiment(n=input, run_simulation=True, exp_dict=exp, noisy=False, dist=dist, shots=shots)

                logger.info(f'{{"Profiler":"Algorithm Runtime", "runtime":"{time.process_time() - init_time}","Exp":"Qubit Lattice,{input},{shots}"}}')
                # exp["runtimes"]["Algorithm Runtime"].append(time.process_time() - init_time)
                
                supermarq_list = supermarq_metrics.compute_all(qc=circuit)
                logger.info(f'{{"Profiler":"SupermarQ", "metrics":"{supermarq_list}","Exp":"Qubit Lattics,{input},{shots}"}}')
                exp['supermarq_metrics'].append(supermarq_list)

                # Noisy
                init_time = time.process_time()
                exp, circuit, accuracy = qubitLatticeExperiment(n=input, run_simulation=True, exp_dict=exp, noisy=True, dist=dist, shots=shots)
                
                logger.info(f'{{"Profiler":"Algorithm Runtime", "runtime":"{time.process_time() - init_time}","Exp":"Qubit Lattice,{input},{shots}"}}')
                # exp["runtimes"]["Noisy Algorithm Runtime"].append(time.process_time() - init_time)

            except:
                logger.error(f'Error in Qubit Lattice Experiment (input: {input})', exc_info=True)
        
        btq_plotter.calculate_total_algorithm_runtime(exp)

        # save experiments dict
        with open(os.path.join("experiment_data", f"ql_{time.strftime('%Y-%m-%d')}.pkl"), 'wb') as f:
            pickle.dump(exp, f)
        
        exp_list.append(exp)
        
        # save plots
        btq_plotter.plot(exp_dict=exp)
    
    if experiments in ["all", "ph"]:
        #----------------------------------
        print(f"Phase Experiments")

        exp = btq_plotter.get_dict("exp")
        exp['name'] = "Phase"

        for i, input in enumerate(ql_ph_inputs):        
            print("\033[K", f"\t{i+1}/{len(ql_ph_inputs)} - {input}", end='\r')

            try:
                exp['shots'].append(shots)
                exp['size'].append(input)

                # Pure
                init_time = time.process_time()
                exp, circuit, accuracy = phaseExperiment(n=input, run_simulation=True, exp_dict=exp, noisy=False, dist=dist, shots=shots)

                logger.info(f'{{"Profiler":"Algorithm Runtime", "runtime":"{time.process_time() - init_time}","Exp":"Phase,{input},{shots}"}}')
                # exp["runtimes"]["Algorithm Runtime"].append(time.process_time() - init_time)
                
                supermarq_list = supermarq_metrics.compute_all(qc=circuit)
                logger.info(f'{{"Profiler":"SupermarQ", "metrics":"{supermarq_list}","Exp":"Phase,{input},{shots}"}}')
                exp['supermarq_metrics'].append(supermarq_list)
                
                # Noisy
                init_time = time.process_time()
                exp, circuit, accuracy = phaseExperiment(n=input, run_simulation=True, exp_dict=exp, noisy=True, dist=dist, shots=shots)
                
                logger.info(f'{{"Profiler":"Algorithm Runtime", "runtime":"{time.process_time() - init_time}","Exp":"Phase,{input},{shots}"}}')
                # exp["runtimes"]["Noisy Algorithm Runtime"].append(time.process_time() - init_time)

            except:
                logger.error(f'Error in Phase Experiment (input: {input})', exc_info=True)
        
        btq_plotter.calculate_total_algorithm_runtime(exp)

        # save experiments dict
        with open(os.path.join("experiment_data", f"ph_{time.strftime('%Y-%m-%d')}.pkl"), 'wb') as f:
            pickle.dump(exp, f)
        
        exp_list.append(exp)
        
        # save plots
        btq_plotter.plot(exp_dict=exp)
    
    if experiments in ["all", "frqi"]:
        #----------------------------------
        print(f"FRQI Experiments")

        exp = btq_plotter.get_dict("exp")
        backend_dict = btq_plotter.get_dict("backend")
        
        exp['name'] = "FRQI"

        for i, input in enumerate(frqi_inputs):
            
            print("\033[K", f"\t{i+1}/{len(frqi_inputs)} - {input}", end='\r')

            try:
                exp['shots'].append(shots)
                exp['size'].append(input)

                # Pure
                init_time = time.process_time()
                exp, circuit, accuracy = frqiExperiment(n=input, run_simulation=True, exp_dict=exp, noisy=False, dist=dist, shots=shots)

                logger.info(f'{{"Profiler":"Algorithm Runtime", "runtime":"{time.process_time() - init_time}","Exp":"FRQI,{input},{shots}"}}')
                # exp["runtimes"]["Algorithm Runtime"].append(time.process_time() - init_time)

                supermarq_list = supermarq_metrics.compute_all(qc=circuit)
                logger.info(f'{{"Profiler":"SupermarQ", "metrics":"{supermarq_list}","Exp":"FRQI,{input},{shots}"}}')
                exp['supermarq_metrics'].append(supermarq_list)

                # Noisy
                init_time = time.process_time()
                exp, circuit, accuracy = frqiExperiment(n=input, run_simulation=True, exp_dict=exp, noisy=True, dist=dist, shots=shots)
                
                logger.info(f'{{"Profiler":"Algorithm Runtime", "runtime":"{time.process_time() - init_time}","Exp":"FRQI,{input},{shots}"}}')
                # exp["runtimes"]["Noisy Algorithm Runtime"].append(time.process_time() - init_time)

                # IBMQ
                # exp = frqiExperiment(n=input, run_simulation=True, exp_dict=exp, noisy=False, dist=dist, backend="ibmq")

            except:
                logger.error(f'Error in FRQI Experiment (input: {input})', exc_info=True)

        btq_plotter.calculate_total_algorithm_runtime(exp)

        # save experiments dict
        with open(os.path.join("experiment_data", f"frqi_{time.strftime('%Y-%m-%d')}.pkl"), 'wb') as f:
            pickle.dump(exp, f)

        exp_list.append(exp)
        
        # save plots
        btq_plotter.plot(exp_dict=exp)

    # save exp_list
    if experiments == "all":
        print(f"Comparatives")
        with open(os.path.join("experiment_data", f"exp_{time.strftime('%Y-%m-%d')}.pkl"), 'wb') as f:
            pickle.dump(exp_list, f)

        btq_plotter.plot_compare(exp_list)
    
    if experiments in ["all", "shots"]:
        #----------------------------------
        print(f"FRQI - Shots Experiments")
        
        shots_dict = btq_plotter.get_dict("shots")

        for i, shot in enumerate(sorted(set([5000, 10000, 25000, 50000, 75000, 100000] + [shots]))):
            
            print("\033[K", f"\t{i+1}/{len([5000, 10000, 25000, 50000, 75000, 100000] + [shots]) - 1} - {shot}", end='\r')

            try:
                shots_dict['shots'].append(shot)

                init_time = time.process_time()
                _, __, accuracy = frqiExperiment(n=256, shots=shot, run_simulation=True, noisy=False, dist=dist)
                shots_dict["accuracy"].append(accuracy)
                shots_dict["runtimes"].append(time.process_time() - init_time)
                
                logger.info(f'{{"Profiler":"Algorithm Runtime", "runtime":"{time.process_time() - init_time}","Exp":"FRQI_shots,256,{shot}"}}')

            except:
                logger.error(f'Error in FRQI Shots Experiment (shot: {shot})', exc_info=True)
        
        # save experiments dict
        with open(os.path.join("experiment_data", f"frqi_shots_{time.strftime('%Y-%m-%d')}.pkl"), 'wb') as f:
            pickle.dump(shots_dict, f)
        
        # save plots
        btq_plotter.plot(shots_dict=shots_dict)

    # =========================
    elif experiments == "ibmq":
        print(f"FRQI IBMQ Experiments")
        
        qiskitService = getIBMQService()

        print(f"Backend setup: {ibmq_backend.name}")

        exp = btq_plotter.get_dict("ibmq")
        shots = 10000

        custom_ibmq_experiment_dict = {
            "name": None,
            "size": [4, 16, 64],
            "shots": [],
            "runtimes":{
                "Encoder": [],
                "Transpile": [],
                "Simulate": [],
                "Decoder": [],
                "Algorithm Runtime": [],
            },
            "depths": {
                "Encoder": [],
                "Invert + Measurement": [],
                "Transpile": []
            },
            "widths": [],
            "accuracy": [],
            "fidelities": [],
            "supermarq_metrics": [],
            "count_ops": [],
            "data_points": [],
            "jobs": ["crvmvfndbt40008jvh50", "crvmvqyx484g008fa9pg", "crvmwxvy7jt000807jgg"],
            "results": [],
            "stateVectors": [],
            "meta_Data": None
        }

        # IBMQ
        if "submit" in sys.argv:
            ibmq_backend = setupIBMQBackend()

            for i, _input in enumerate(frqi_inputs[-1:]):
                print("\033[K", f"\t{i+1}/{len(frqi_inputs)} - {_input}", end='\r')

                exp['size'].append(_input)

                # input()

                init_time = time.process_time()
                exp = frqiExperimentIBMQ(n=_input, dist=dist, shots=shots, mode="submit", exp_dict=exp)
                
                logger.info(f'{{"Profiler":"Algorithm Runtime", "runtime":"{time.process_time() - init_time}","Exp":"FRQI_backend,{_input},{shots}"}}')
                # exp["runtimes"][i].append(time.process_time() - init_time)
                # exp["accuracy"].append(accuracy)

            with open(os.path.join("experiment_data", f"exp_ibmq_{time.strftime('%Y-%m-%d')}.pkl"), 'wb') as f:
                pickle.dump(exp_list, f)


        elif "decode" in sys.argv:
            exp = frqiExperimentIBMQ(n=0, dist=dist, shots=shots, mode="decode", exp_dict=custom_ibmq_experiment_dict)
            print(exp['runtimes'], exp['accuracy'])

