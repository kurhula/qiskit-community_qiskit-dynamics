# This code is part of Qiskit.
#
# (C) Copyright IBM 2022.
#
# This code is licensed under the Apache License, Version 2.0. You may
# obtain a copy of this license in the LICENSE.txt file in the root directory
# of this source tree or at http://www.apache.org/licenses/LICENSE-2.0.
#
# Any modifications or derivative works of this code must retain this
# copyright notice, and modified files need to carry a notice indicating
# that they have been altered from the originals.
# pylint: disable=invalid-name

"""
Test DynamicsBackend.
"""

from types import SimpleNamespace
from itertools import product

import numpy as np
from scipy.integrate._ivp.ivp import OdeResult
from scipy.linalg import expm

from qiskit import QiskitError, pulse, QuantumCircuit, QuantumRegister, ClassicalRegister
from qiskit.circuit.library import XGate, UnitaryGate, Measure
from qiskit.transpiler import Target, InstructionProperties
from qiskit.quantum_info import Statevector, DensityMatrix, Operator, SuperOp
from qiskit.result.models import ExperimentResult, ExperimentResultData
from qiskit.providers.models.backendconfiguration import UchannelLO
from qiskit.providers.backend import QubitProperties

from qiskit_dynamics import Solver, DynamicsBackend
from qiskit_dynamics.backend import default_experiment_result_function
from qiskit_dynamics.backend.dynamics_backend import (
    _get_acquire_instruction_timings,
    _get_backend_channel_freqs,
)
from ..common import QiskitDynamicsTestCase


class TestDynamicsBackendValidation(QiskitDynamicsTestCase):
    """Test validation checks."""

    def setUp(self):
        """Build simple simulator for multiple tests."""

        solver = Solver(
            static_hamiltonian=np.array([[1.0, 0.0], [0.0, -1.0]]),
            hamiltonian_operators=[np.array([[0.0, 1.0], [1.0, 0.0]])],
            hamiltonian_channels=["d0"],
            channel_carrier_freqs={"d0": 1.0},
            dt=1.0,
        )

        self.simple_backend = DynamicsBackend(solver=solver)

    def test_solver_not_configured_for_pulse(self):
        """Test error is raised if solver not configured for pulse simulation."""

        solver = Solver(
            static_hamiltonian=np.array([[1.0, 0.0], [0.0, -1.0]]),
            hamiltonian_operators=[np.array([[0.0, 1.0], [1.0, 0.0]])],
        )

        with self.assertRaisesRegex(QiskitError, "not configured for Pulse"):
            DynamicsBackend(solver=solver)

    def test_run_input_error(self):
        """Test submission of invalid run input."""

        with self.assertRaisesRegex(QiskitError, "not supported by DynamicsBackend.run."):
            self.simple_backend.run([1])

    def test_subsystem_dims_inconsistency(self):
        """Test that setting subsystem_dims inconsistently with solver.model.dim raises error."""

        with self.assertRaisesRegex(QiskitError, "inconsistent"):
            self.simple_backend.set_options(subsystem_dims=[4])

    def test_max_outcome_level_error(self):
        """Test that invalid max_outcome_level results in error."""

        with self.assertRaisesRegex(QiskitError, "must be a positive integer"):
            self.simple_backend.set_options(max_outcome_level=0)

        with self.assertRaisesRegex(QiskitError, "must be a positive integer"):
            self.simple_backend.set_options(max_outcome_level="hi")

    def test_no_measurements_in_schedule(self):
        """Test that running a schedule with no measurements raises an error."""

        with pulse.build() as schedule:
            pulse.play(pulse.Waveform([0.5, 0.5, 0.5]), pulse.DriveChannel(0))

        with self.assertRaisesRegex(QiskitError, "At least one measurement"):
            self.simple_backend.run(schedule)

    def test_no_measurements_with_memory_slots_in_schedule(self):
        """Test that running a schedule without measurements saving results in a MemorySlot
        raises an error."""

        with pulse.build() as schedule:
            pulse.play(pulse.Waveform([0.5, 0.5, 0.5]), pulse.DriveChannel(0))
            pulse.acquire(duration=1, qubit_or_channel=0, register=pulse.RegisterSlot(0))

        with self.assertRaisesRegex(QiskitError, "At least one measurement"):
            self.simple_backend.run(schedule)

    def test_multiple_measurements_in_schedule(self):
        """Test error raising when attempting to run a schedule with multiple measurements."""

        with pulse.build() as schedule:
            with pulse.align_right():
                pulse.play(pulse.Waveform([0.5, 0.5, 0.5]), pulse.DriveChannel(0))
                pulse.acquire(duration=1, qubit_or_channel=0, register=pulse.MemorySlot(0))
                pulse.play(pulse.Waveform([0.5, 0.5, 0.5]), pulse.DriveChannel(0))
                pulse.acquire(duration=1, qubit_or_channel=0, register=pulse.MemorySlot(0))

        with self.assertRaisesRegex(QiskitError, "only supports measurements at one time"):
            self.simple_backend.run(schedule)

    def test_measure_nonexistant_subsystem(self):
        """Attempt to measure subsystem that doesn't exist."""

        with pulse.build() as schedule:
            pulse.play(pulse.Waveform([0.5, 0.5, 0.5]), pulse.DriveChannel(0))
            pulse.acquire(duration=1, qubit_or_channel=1, register=pulse.MemorySlot(0))

        with self.assertRaisesRegex(QiskitError, "Attempted to measure out of bounds subsystem 1."):
            self.simple_backend.run(schedule)

    def test_measure_trivial_subsystem(self):
        """Attempt to measure subsystem with dimension 1."""

        with pulse.build() as schedule:
            pulse.play(pulse.Waveform([0.5, 0.5, 0.5]), pulse.DriveChannel(0))
            pulse.acquire(duration=1, qubit_or_channel=1, register=pulse.MemorySlot(0))

        self.simple_backend.set_options(subsystem_dims=[2, 1])
        with self.assertWarnsRegex(Warning, "Measuring trivial subsystem 1"):
            self.simple_backend.run(schedule)

    def test_invalid_initial_state(self):
        """Test setting an invalid initial state."""

        with self.assertRaisesRegex(QiskitError, "initial_state must be either"):
            self.simple_backend.set_options(initial_state=1)

    def test_invalid_meas_level(self):
        """Test setting an invalid meas_level."""

        with self.assertRaisesRegex(QiskitError, "Only meas_level 1 and 2 are supported"):
            self.simple_backend.set_options(meas_level=0)

    def test_invalid_meas_return(self):
        """Test setting an invalid meas_return."""

        with self.assertRaisesRegex(QiskitError, "meas_return must be either 'single' or 'avg'"):
            self.simple_backend.set_options(meas_return="combined")

    def test_invalid_iq_width(self):
        """Test setting an invalid iq_width."""

        with self.assertRaisesRegex(QiskitError, "must be a positive float"):
            self.simple_backend.set_options(iq_width=0)
        with self.assertRaisesRegex(QiskitError, "must be a positive float"):
            self.simple_backend.set_options(iq_width="hi")

    def test_invalid_iq_centers(self):
        """Test setting an invalid iq_centers."""

        with self.assertRaisesRegex(QiskitError, "iq_centers option must be either None or"):
            self.simple_backend.set_options(iq_centers=[[0]])

        with self.assertRaisesRegex(QiskitError, "iq_centers option is not consistent"):
            self.simple_backend.set_options(subsystem_dims=[2])
            self.simple_backend.set_options(iq_centers=[[[1, 0], [0, 1], [1, 1]]])

        with self.assertRaisesRegex(QiskitError, "iq_centers option is not consistent"):
            self.simple_backend.set_options(subsystem_dims=[2])
            self.simple_backend.set_options(iq_centers=[[[1, 0], [0, 1]], [[1, 0], [0, 1]]])

    def test_invalid_experiment_result_function(self):
        """Test setting a non-callable experiment_result_function."""

        with self.assertRaisesRegex(QiskitError, "must be callable."):
            self.simple_backend.set_options(experiment_result_function=1)

    def test_invalid_configuration_type(self):
        """Test setting non-PulseBackendConfiguration."""

        with self.assertRaisesRegex(QiskitError, "configuration option must be"):
            self.simple_backend.set_options(configuration=1)

    def test_invalid_defaults_type(self):
        """Test setting non-PulseDefaults."""

        with self.assertRaisesRegex(QiskitError, "defaults option must be"):
            self.simple_backend.set_options(defaults=1)

    def test_not_implemented_control_channel_map(self):
        """Test raising of NotImplementError if control_channel called when no control_channel_map
        specified.
        """

        with self.assertRaises(NotImplementedError):
            self.simple_backend.control_channel((0, 1))

    def test_invalid_control_channel_map(self):
        """Test setting an invalid control_channel_map raises an error."""

        with self.assertRaisesRegex(QiskitError, "None or a dictionary"):
            self.simple_backend.set_options(control_channel_map=1)

        with self.assertRaisesRegex(QiskitError, "values must be of type int"):
            self.simple_backend.set_options(control_channel_map={(0, 1): "3"})

    def test_invalid_drive_channel(self):
        """Test requesting an invalid drive channel."""

        with self.assertRaisesRegex(QiskitError, "drive_channel requested for qubit 10"):
            self.simple_backend.drive_channel(10)

    def test_invalid_control_channel(self):
        """Test requesting an invalid control channel."""

        self.simple_backend.set_options(control_channel_map={(0, 1): 0})

        with self.assertRaisesRegex(QiskitError, "Key wow not in control_channel_map."):
            self.simple_backend.control_channel("wow")


class TestDynamicsBackend(QiskitDynamicsTestCase):
    """Tests ensuring basic workflows work correctly for DynamicsBackend."""

    def setUp(self):
        """Build reusable models."""

        static_ham = 2 * np.pi * 5 * np.array([[-1.0, 0.0], [0.0, 1.0]]) / 2
        drive_op = 2 * np.pi * 0.1 * np.array([[0.0, 1.0], [1.0, 0.0]]) / 2

        solver = Solver(
            static_hamiltonian=static_ham,
            hamiltonian_operators=[drive_op],
            hamiltonian_channels=["d0"],
            channel_carrier_freqs={"d0": 5.0},
            dt=0.1,
            rotating_frame=static_ham,
        )

        self.simple_solver = solver
        self.simple_backend = DynamicsBackend(solver=solver)

        ident = np.eye(2, dtype=complex)
        static_ham_2q = (
            2 * np.pi * 4.99 * np.kron(ident, np.array([[-1.0, 0.0], [0.0, 1.0]])) / 2
            + 2 * np.pi * 5.01 * np.kron(np.array([[-1.0, 0.0], [0.0, 1.0]]), ident) / 2
            + 2
            * np.pi
            * 0.002
            * np.kron(np.array([[0.0, 1.0], [0.0, 0.0]]), np.array([[0.0, 0.0], [1.0, 0.0]]))
            + 2
            * np.pi
            * 0.002
            * np.kron(np.array([[0.0, 0.0], [1.0, 0.0]]), np.array([[0.0, 1.0], [0.0, 0.0]]))
        )
        drive_op0 = 2 * np.pi * 0.1 * np.kron(ident, np.array([[0.0, 1.0], [1.0, 0.0]])) / 2
        drive_op1 = 2 * np.pi * 0.1 * np.kron(np.array([[0.0, 1.0], [1.0, 0.0]]), ident) / 2
        solver_2q = Solver(
            static_hamiltonian=static_ham_2q,
            hamiltonian_operators=[drive_op0, drive_op1],
            hamiltonian_channels=["d0", "d1"],
            channel_carrier_freqs={"d0": 4.99, "d1": 5.01},
            dt=0.1,
            rotating_frame=static_ham_2q,
        )
        self.solver_2q = solver_2q
        self.backend_2q = DynamicsBackend(solver=solver_2q, subsystem_dims=[2, 2])

        # function to discriminate 0 and 1 for default centers.
        self.iq_to_counts = lambda iq_n: dict(
            zip(*np.unique(["0" if iq[0].real > 0 else "1" for iq in iq_n], return_counts=True))
        )

    def test_pi_pulse(self):
        """Test simulation of a pi pulse."""

        with pulse.build() as schedule:
            with pulse.align_right():
                pulse.play(pulse.Waveform([1.0] * 100), pulse.DriveChannel(0))
                pulse.acquire(duration=1, qubit_or_channel=0, register=pulse.MemorySlot(0))

        result = self.simple_backend.run(schedule, seed_simulator=1234567).result()
        self.assertDictEqual(result.get_counts(), {"1": 1024})
        self.assertTrue(result.get_memory() == ["1"] * 1024)

        result = self.simple_backend.run(
            schedule, meas_level=1, meas_return="single", seed_simulator=1234567
        ).result()
        counts = self.iq_to_counts(result.get_memory())
        self.assertDictEqual(counts, {"1": 1024})

    def test_solve(self):
        """Test the ODE simulation with different input and y0 types using a X pulse."""

        # build solver to use in the test
        static_ham = 2 * np.pi * 5 * np.array([[-1.0, 0.0], [0.0, 1.0]]) / 2
        drive_op = 2 * np.pi * 0.1 * np.array([[0.0, 1.0], [1.0, 0.0]]) / 2

        solver = Solver(
            static_hamiltonian=static_ham,
            hamiltonian_operators=[drive_op],
            hamiltonian_channels=["d0"],
            channel_carrier_freqs={"d0": 5.0},
            dt=0.1,
            rotating_frame=static_ham,
            rwa_cutoff_freq=5.0,
        )

        backend = DynamicsBackend(solver=solver, solver_options={"atol": 1e-10, "rtol": 1e-10})

        # create the circuit, pulse schedule and calibrate the gate
        x_circ0 = QuantumCircuit(1)
        x_circ0.x(0)
        n_samples = 5
        with pulse.build() as x_sched0:
            pulse.play(pulse.Waveform([1.0] * n_samples), pulse.DriveChannel(0))
        x_circ0.add_calibration("x", [0], x_sched0)

        # create the initial states and expected simulation results
        generator = np.array([[0, 1], [1, 0]], dtype=np.complex128)
        rotation_strength = n_samples / 100
        expected_unitary = expm(-1.0j * 0.5 * np.pi * rotation_strength * generator)
        y0_and_expected_results = []
        for y0_type in [Statevector, Operator, DensityMatrix, SuperOp]:
            y0 = y0_type(QuantumCircuit(1))
            expected_result = y0_type(UnitaryGate(expected_unitary))
            y0_and_expected_results.append((y0, expected_result))
        # y0=None defaults to Statevector
        y0_and_expected_results.append(
            (None, Statevector(QuantumCircuit(1)).evolve(expected_unitary))
        )
        # y0 is a np.array, we expect a np.array as a result
        y0_and_expected_results.append((np.eye(static_ham.shape[0]), expected_unitary))
        input_variety = [x_sched0, x_circ0]

        # solve for all combinations of input types and initial states
        for solve_input, (y0, expected_result), t_span in product(
            input_variety, y0_and_expected_results, ([0, n_samples * backend.dt], None)
        ):
            solver_results = backend.solve(
                t_span=t_span,
                y0=y0,
                solve_input=[solve_input],
            )

            # results are always a list
            for solver_result in solver_results:
                self.assertTrue(solver_result.success)
                self.assertAllClose(solver_result.y[-1], expected_result, atol=1e-8, rtol=1e-8)
                self.assertEqual(solver_result.t[-1], n_samples * backend.dt)

    def test_pi_pulse_initial_state(self):
        """Test simulation of a pi pulse with a different initial state."""

        with pulse.build() as schedule:
            with pulse.align_right():
                pulse.play(pulse.Waveform([1.0] * 100), pulse.DriveChannel(0))
                pulse.acquire(duration=1, qubit_or_channel=0, register=pulse.MemorySlot(0))

        result = self.simple_backend.run(
            schedule, seed_simulator=1234567, initial_state=Statevector([0.0, 1.0])
        ).result()
        self.assertDictEqual(result.get_counts(), {"0": 1024})
        self.assertTrue(result.get_memory() == ["0"] * 1024)

    def test_pi_half_pulse_density_matrix(self):
        """Test simulation of a pi/2 pulse with a DensityMatrix."""

        with pulse.build() as schedule:
            with pulse.align_right():
                pulse.play(pulse.Waveform([1.0] * 50), pulse.DriveChannel(0))
                pulse.acquire(duration=1, qubit_or_channel=0, register=pulse.MemorySlot(0))

        result = self.simple_backend.run(
            schedule, seed_simulator=398472, initial_state=DensityMatrix([1.0, 0.0])
        ).result()
        self.assertDictEqual(result.get_counts(), {"0": 513, "1": 511})

        result = result = self.simple_backend.run(
            schedule,
            seed_simulator=398472,
            initial_state=DensityMatrix([1.0, 0.0]),
            meas_level=1,
            meas_return="single",
        ).result()

        counts = self.iq_to_counts(result.get_memory())
        self.assertDictEqual(counts, {"0": 510, "1": 514})

    def test_circuit_with_pulse_defs(self):
        """Test simulating a circuit with pulse definitions."""

        circ = QuantumCircuit(1, 1)
        circ.x(0)
        circ.measure([0], [0])

        with pulse.build() as x_sched0:
            pulse.play(pulse.Waveform([1.0] * 100), pulse.DriveChannel(0))

        circ.add_calibration("x", [0], x_sched0)

        result = self.simple_backend.run(circ, seed_simulator=1234567).result()
        self.assertDictEqual(result.get_counts(), {"1": 1024})
        self.assertTrue(result.get_memory() == ["1"] * 1024)

    def test_circuit_with_multiple_classical_registers(self):
        """Test simulating a circuit with pulse definitions and multiple classical registers."""

        circ = QuantumCircuit(QuantumRegister(1), ClassicalRegister(1), ClassicalRegister(1))
        circ.x(0)
        circ.measure([0], [1])

        with pulse.build() as x_sched0:
            pulse.play(pulse.Waveform([0.0]), pulse.DriveChannel(0))

        circ.add_calibration("x", [0], x_sched0)

        result = self.simple_backend.run(circ, seed_simulator=1234567).result()
        self.assertTrue(all(x == "0x0" for x in result.to_dict()["results"][0]["data"]["memory"]))

    def test_circuit_with_target_pulse_instructions(self):
        """Test running a circuit on a simulator with defined instructions."""

        # build target into simulator
        with pulse.build() as x_sched0:
            pulse.play(pulse.Waveform([1.0] * 100), pulse.DriveChannel(0))

        target = Target()
        target.add_instruction(XGate(), {(0,): InstructionProperties(calibration=x_sched0)})

        backend = DynamicsBackend(solver=self.simple_solver, target=target)

        # build and run circuit
        circ = QuantumCircuit(1, 1)
        circ.x(0)
        circ.measure([0], [0])

        result = backend.run(circ, seed_simulator=1234567).result()
        self.assertDictEqual(result.get_counts(), {"1": 1024})
        self.assertTrue(result.get_memory() == ["1"] * 1024)

    def test_circuit_memory_slot_num(self):
        """Test correct memory_slot number based on quantum circuit."""

        # build a pair of non-trivial 2q circuits with 5 memoryslots, saving measurements
        # in different memory slots
        circ0 = QuantumCircuit(2, 5)
        circ0.x(0)
        circ0.h(1)
        circ0.measure([0, 1], [0, 1])

        circ1 = QuantumCircuit(2, 5)
        circ1.x(0)
        circ1.h(1)
        circ1.measure([0, 1], [2, 4])

        # add definitions to instruction_schedule_map
        inst_map = self.backend_2q.instruction_schedule_map
        with pulse.build() as x_sched0:
            pulse.play(pulse.Waveform([1.0] * 100), pulse.DriveChannel(0))

        with pulse.build() as h_sched1:
            pulse.play(pulse.Waveform([1.0] * 50), pulse.DriveChannel(1))

        inst_map.add("x", qubits=0, schedule=x_sched0)
        inst_map.add("h", qubits=1, schedule=h_sched1)

        # run both
        result0 = self.backend_2q.run(circ0, seed_simulator=1234567).result()
        result1 = self.backend_2q.run(circ1, seed_simulator=1234567).result()

        # extract results from both experiments and validate consistency
        # results object converts memory into binary
        result0_dict = result0.get_counts()
        result1_dict = result1.get_counts()

        self.assertEqual(result0_dict["1"], result1_dict["100"])
        self.assertEqual(result0_dict["11"], result1_dict["10100"])
        self.assertEqual(result0_dict["10"], result1_dict["10000"])

    def test_schedule_memory_slot_num(self):
        """Test correct memory_slot number in schedule."""

        with pulse.build() as schedule0:
            with pulse.align_right():
                pulse.play(pulse.Waveform([1.0] * 100), pulse.DriveChannel(0))
                pulse.play(pulse.Waveform([1.0] * 50), pulse.DriveChannel(1))
                pulse.acquire(duration=1, qubit_or_channel=0, register=pulse.MemorySlot(0))
                pulse.acquire(duration=1, qubit_or_channel=1, register=pulse.MemorySlot(1))

        with pulse.build() as schedule1:
            with pulse.align_right():
                pulse.play(pulse.Waveform([1.0] * 100), pulse.DriveChannel(0))
                pulse.play(pulse.Waveform([1.0] * 50), pulse.DriveChannel(1))
                pulse.acquire(duration=1, qubit_or_channel=0, register=pulse.MemorySlot(2))
                pulse.acquire(duration=1, qubit_or_channel=1, register=pulse.MemorySlot(4))

        # run both
        result0 = self.backend_2q.run(schedule0, seed_simulator=1234567).result()
        result1 = self.backend_2q.run(schedule1, seed_simulator=1234567).result()

        # extract results from both experiments and validate consistency
        # results object converts memory into binary
        result0_dict = result0.get_counts()
        result1_dict = result1.get_counts()

        self.assertEqual(result0_dict["1"], result1_dict["100"])
        self.assertEqual(result0_dict["11"], result1_dict["10100"])
        self.assertEqual(result0_dict["10"], result1_dict["10000"])

        result0_iq = (
            self.backend_2q.run(schedule0, meas_level=1, seed_simulator=1234567)
            .result()
            .get_memory()
        )
        result1_iq = (
            self.backend_2q.run(schedule1, meas_level=1, seed_simulator=1234567)
            .result()
            .get_memory()
        )

        self.assertTrue(result0_iq.shape == (2,))
        self.assertTrue(result1_iq.shape == (5,))
        self.assertAllClose(result0_iq, result1_iq[[2, 4]])

    def test_measure_higher_levels(self):
        """Test measurement of higher levels."""

        solver = Solver(static_hamiltonian=np.diag([-1.0, 0.0, 1.0]), dt=0.1)
        qutrit_backend = DynamicsBackend(
            solver=solver, max_outcome_level=2, initial_state=Statevector([0.0, 0.0, 1.0])
        )

        circ = QuantumCircuit(1, 1)
        circ.measure([0], [0])

        res = qutrit_backend.run(circ).result()

        self.assertTrue(all(x == "0x2" for x in res.to_dict()["results"][0]["data"]["memory"]))

    def test_setting_experiment_result_function(self):
        """Test overriding default experiment_result_function."""

        # trivial result function
        # pylint: disable=unused-argument
        def exp_result_function(*args, **kwargs):
            return ExperimentResult(
                data=ExperimentResultData(counts={"3": 1}), shots=1, success=True
            )

        # minimal simulation schedule
        with pulse.build() as schedule:
            with pulse.align_right():
                pulse.play(pulse.Waveform([1.0] * 1), pulse.DriveChannel(0))
                pulse.acquire(duration=1, qubit_or_channel=0, register=pulse.MemorySlot(0))

        result = self.simple_backend.run(
            schedule,
            seed_simulator=1234567,
            initial_state=Statevector([0.0, 1.0]),
            experiment_result_function=exp_result_function,
        ).result()
        self.assertDictEqual(result.get_counts(), {"3": 1})

    def test_drive_channel(self):
        """Test drive_channel method."""

        channel = self.simple_backend.drive_channel(0)
        self.assertTrue(isinstance(channel, pulse.DriveChannel))
        self.assertTrue(channel.index == 0)

    def test_control_channel(self):
        """Test setting control_channel_map and retriving channel via the control_channel method."""

        self.simple_backend.set_options(control_channel_map={(0, 1): 1})

        channel = self.simple_backend.control_channel((0, 1))
        self.assertTrue(isinstance(channel, list))
        self.assertTrue(len(channel) == 1)

        channel = channel[0]
        self.assertTrue(isinstance(channel, pulse.ControlChannel))
        self.assertTrue(channel.index == 1)

    def test_metadata_transfer(self):
        """Test that circuit metadata is correctly stored in the result object."""

        solver = Solver(static_hamiltonian=np.diag([-1.0, 0.0, 1.0]), dt=0.1)
        qutrit_backend = DynamicsBackend(
            solver=solver, max_outcome_level=2, initial_state=Statevector([0.0, 0.0, 1.0])
        )

        circ0 = QuantumCircuit(1, 1, metadata={"key0": "value0"})
        circ0.measure([0], [0])
        circ1 = QuantumCircuit(1, 1, metadata={"key1": "value1"})
        circ1.measure([0], [0])

        res = qutrit_backend.run([circ0, circ1]).result()
        self.assertTrue(all(x == "0x2" for x in res.to_dict()["results"][0]["data"]["memory"]))
        self.assertDictEqual(res.results[0].header.metadata, {"key0": "value0"})
        self.assertTrue(all(x == "0x2" for x in res.to_dict()["results"][1]["data"]["memory"]))
        self.assertDictEqual(res.results[1].header.metadata, {"key1": "value1"})

    def test_valid_measurement_properties(self):
        """Test that DynamicsBackend instantiation always carries measurement instructions."""

        # Case where no measurement instruction is added manually
        instruction_schedule_map = self.backend_2q.target.instruction_schedule_map()
        for q in range(self.simple_backend.num_qubits):
            self.assertTrue(instruction_schedule_map.has(instruction="measure", qubits=q))
            self.assertTrue(
                isinstance(
                    instruction_schedule_map.get("measure", q).instructions[0][1], pulse.Acquire
                )
            )
            self.assertEqual(len(instruction_schedule_map.get("measure", q).instructions), 1)

        # Case where measurement instruction is added manually
        custom_meas_duration = 3
        with pulse.build() as meas_sched0:
            pulse.acquire(
                duration=custom_meas_duration, qubit_or_channel=0, register=pulse.MemorySlot(0)
            )

        with pulse.build() as meas_sched1:
            pulse.acquire(
                duration=custom_meas_duration, qubit_or_channel=1, register=pulse.MemorySlot(1)
            )

        measure_properties = {
            (0,): InstructionProperties(calibration=meas_sched0),
            (1,): InstructionProperties(calibration=meas_sched1),
        }
        target = Target()
        target.add_instruction(Measure(), measure_properties)
        custom_meas_backend = DynamicsBackend(
            solver=self.solver_2q, target=target, subsystem_dims=[2, 2]
        )
        instruction_schedule_map = custom_meas_backend.target.instruction_schedule_map()
        for q in range(self.simple_backend.num_qubits):
            self.assertTrue(instruction_schedule_map.has(instruction="measure", qubits=q))
            self.assertTrue(
                isinstance(
                    instruction_schedule_map.get("measure", q).instructions[0][1], pulse.Acquire
                )
            )
            self.assertEqual(len(instruction_schedule_map.get("measure", q).instructions), 1)
            self.assertEqual(
                instruction_schedule_map.get("measure", q).instructions[0][1].duration,
                custom_meas_duration,
            )


class Test_default_experiment_result_function(QiskitDynamicsTestCase):
    """Test default_experiment_result_function."""

    def setUp(self):
        """Build reusable models."""

        static_ham = 2 * np.pi * 5 * np.array([[-1.0, 0.0], [0.0, 1.0]]) / 2
        drive_op = 2 * np.pi * 0.1 * np.array([[0.0, 1.0], [1.0, 0.0]]) / 2

        solver = Solver(
            static_hamiltonian=static_ham,
            hamiltonian_operators=[drive_op],
            hamiltonian_channels=["d0"],
            channel_carrier_freqs={"d0": 5.0},
            dt=0.1,
            rotating_frame=static_ham,
        )

        self.simple_solver = solver
        self.simple_backend = DynamicsBackend(solver=solver)

    def test_simple_example(self):
        """Test a simple example."""

        output = default_experiment_result_function(
            experiment_name="exp123",
            solver_result=OdeResult(
                t=[0.0, 1.0], y=[Statevector([1.0, 0.0]), Statevector(np.sqrt([0.5, 0.5]))]
            ),
            measurement_subsystems=[0],
            memory_slot_indices=[1],
            num_memory_slots=3,
            backend=self.simple_backend,
            seed=1234567,
        )
        expected = {"000": 513, "010": 511}

        self.assertDictEqual(output.data.counts, {hex(int(k, 2)): v for k, v in expected.items()})


class Test_get_channel_backend_freqs(QiskitDynamicsTestCase):
    """Test cases for _get_channel_backend_freqs."""

    def setUp(self):
        """Setup a simple configuration and default."""

        defaults = SimpleNamespace()
        defaults.qubit_freq_est = [0.343, 1.131, 2.1232, 3.3534, 4.123, 5.3532]
        defaults.meas_freq_est = [0.23432, 1.543, 2.543, 3.543, 4.1321, 5.5433]
        self.defaults = defaults

        config = SimpleNamespace()
        config.u_channel_lo = [
            [UchannelLO(q=0, scale=1.0), UchannelLO(q=1, scale=-1.0)],
            [UchannelLO(q=3, scale=2.1)],
            [UchannelLO(q=4, scale=1.1), UchannelLO(q=2, scale=-1.1)],
        ]
        self.config = config

    def _test_with_setUp_example_no_target(self, channels, expected_output):
        """Test with defaults and config from setUp."""
        self.assertDictEqual(
            _get_backend_channel_freqs(
                backend_target=None,
                backend_config=self.config,
                backend_defaults=self.defaults,
                channels=channels,
            ),
            expected_output,
        )

    def test_drive_channels(self):
        """Test case with just drive channels."""
        channels = ["d0", "d1", "d2"]
        expected_output = {f"d{idx}": self.defaults.qubit_freq_est[idx] for idx in range(3)}
        self._test_with_setUp_example_no_target(channels=channels, expected_output=expected_output)

    def test_drive_and_meas_channels(self):
        """Test case drive and meas channels."""
        channels = ["d0", "d1", "d2", "m0", "m3"]
        expected_output = {f"d{idx}": self.defaults.qubit_freq_est[idx] for idx in range(3)}
        expected_output.update({f"m{idx}": self.defaults.meas_freq_est[idx] for idx in [0, 3]})
        self._test_with_setUp_example_no_target(channels=channels, expected_output=expected_output)

    def test_drive_and_u_channels(self):
        """Test case drive and u channels."""
        channels = ["d0", "d1", "d2", "u1", "u2"]
        expected_output = {f"d{idx}": self.defaults.qubit_freq_est[idx] for idx in range(3)}
        expected_output.update(
            {
                "u1": 2.1 * self.defaults.qubit_freq_est[3],
                "u2": 1.1 * self.defaults.qubit_freq_est[4] - 1.1 * self.defaults.qubit_freq_est[2],
            }
        )
        self._test_with_setUp_example_no_target(channels=channels, expected_output=expected_output)

    def test_unrecognized_channel_type(self):
        """Test error is raised if unrecognized channel type."""

        with self.assertRaisesRegex(QiskitError, "Unrecognized"):
            _get_backend_channel_freqs(
                backend_target=None,
                backend_config=SimpleNamespace(),
                backend_defaults=SimpleNamespace(),
                channels=["r1"],
            )

    def test_no_qubit_freq_est_attribute_error(self):
        """Test error if no qubit_freq_est in defaults."""

        with self.assertRaisesRegex(QiskitError, "frequencies not available in target or defaults"):
            _get_backend_channel_freqs(
                backend_target=None,
                backend_config=SimpleNamespace(),
                backend_defaults=None,
                channels=["d0"],
            )

    def test_no_meas_freq_est_attribute_error(self):
        """Test error if no meas_freq_est in defaults."""

        with self.assertRaisesRegex(QiskitError, "defaults does not have"):
            _get_backend_channel_freqs(
                backend_target=None,
                backend_config=SimpleNamespace(),
                backend_defaults=None,
                channels=["m0"],
            )

    def test_missing_u_channel_error(self):
        """Raise error if missing u channel."""
        with self.assertRaisesRegex(QiskitError, "ControlChannel index 4"):
            _get_backend_channel_freqs(
                backend_target=None,
                backend_config=self.config,
                backend_defaults=self.defaults,
                channels=["u4"],
            )

    def test_drive_out_of_bounds(self):
        """Raise error if drive channel index too high."""
        with self.assertRaisesRegex(QiskitError, "DriveChannel index 10"):
            _get_backend_channel_freqs(
                backend_target=None,
                backend_config=self.config,
                backend_defaults=self.defaults,
                channels=["d10"],
            )

    def test_meas_out_of_bounds(self):
        """Raise error if drive channel index too high."""
        with self.assertRaisesRegex(QiskitError, "MeasureChannel index 6"):
            _get_backend_channel_freqs(
                backend_target=None,
                backend_config=self.config,
                backend_defaults=self.defaults,
                channels=["m6"],
            )

    def test_no_defaults(self):
        """Test a case where defaults are not needed."""
        target = Target(
            dt=0.1,
            qubit_properties=[QubitProperties(frequency=0.0), QubitProperties(frequency=1.0)],
        )

        config = SimpleNamespace()
        config.u_channel_lo = []

        channel_freqs = _get_backend_channel_freqs(
            backend_target=target,
            backend_config=config,
            backend_defaults=None,
            channels=["d0", "d1"],
        )
        self.assertDictEqual(channel_freqs, {"d0": 0.0, "d1": 1.0})


class Test_get_acquire_instruction_timings(QiskitDynamicsTestCase):
    """Tests for _get_acquire_instruction_timings behaviour not covered by DynamicsBackend tests."""

    def test_correct_t_span(self):
        """Validate correct t_span value."""
        with pulse.build() as schedule0:
            with pulse.align_right():
                pulse.play(pulse.Waveform([1.0] * 104), pulse.DriveChannel(0))
                pulse.play(pulse.Waveform([1.0] * 50), pulse.DriveChannel(1))
                pulse.acquire(duration=1, qubit_or_channel=0, register=pulse.MemorySlot(0))

        with pulse.build() as schedule1:
            with pulse.align_right():
                pulse.play(pulse.Waveform([1.0] * 100), pulse.DriveChannel(0))
                pulse.play(pulse.Waveform([1.0] * 50), pulse.DriveChannel(1))
                pulse.acquire(duration=1, qubit_or_channel=1, register=pulse.MemorySlot(1))

        dt = 1 / 4.5e9
        (
            t_span,
            measurement_subsystems_list,
            memory_slot_indices_list,
        ) = _get_acquire_instruction_timings(
            schedules=[schedule0, schedule1], subsystem_dims=[2, 2], dt=dt
        )

        self.assertAllClose(np.array(t_span), np.array([[0.0, 104 * dt], [0.0, 100 * dt]]))
        self.assertTrue(measurement_subsystems_list == [[0], [1]])
        self.assertTrue(memory_slot_indices_list == [[0], [1]])
