from dataclasses import dataclass
from typing import Annotated, Sequence

import matplotlib.pyplot as plt
from numpy import sin, cos, ndarray, array, eye, zeros_like, zeros, clip, arccos, exp, sqrt, arange, rad2deg, deg2rad, \
    mean, stack, std
from numpy.linalg import norm, trace
from numpy.random import normal


def rotation_matrix_from_vector(delta_theta: ndarray) -> ndarray:
    """ Generate a 3x3 rotation matrix from a rotation vector using Rodrigues' formula. """
    norm_delta = norm(delta_theta)
    if norm_delta < 1e-10:
        return eye(3)
    theta_hat = delta_theta / norm_delta
    K = array([
        [0, -theta_hat[2], theta_hat[1]],
        [theta_hat[2], 0, -theta_hat[0]],
        [-theta_hat[1], theta_hat[0], 0]
    ])
    R = (eye(3) + sin(norm_delta) * K +
         (1 - cos(norm_delta)) * K @ K)
    return R
####

@dataclass
class ImuMeasurement:
    timestamp: float
    delta_theta: Annotated[ndarray, (3,)]  # Delta theta (rad)
    delta_v: Annotated[ndarray, (3,)]  # Delta v (m/s)
####

@dataclass(frozen=True)
class IMUNoiseParams:
    """
    Initialize noise and error parameters for IMU.

    Args:
    """
    sigma_wn: float  # White noise density (unit/s/sqrt(Hz))
    sigma_to: float  # Turn-on bias standard deviation (unit/s)
    sigma_b: float  # Bias driving noise standard deviation (unit/s)
    tau: float  # Bias correlation time constant (s)
    x_max: float  # Maximum signal magnitude for nonlinearity (unit/s)
    epsilon: float  # Linear scale factor error (dimensionless)
    alpha: float  # Nonlinear scale factor coefficient (dimensionless)

def rotation_vector_from_matrix(R: ndarray) -> ndarray:
    """ Extract rotation vector from a 3x3 rotation matrix. """
    tr = trace(R)
    cos_theta = (tr - 1) / 2.
    theta = arccos(clip(cos_theta, -1., 1.))

    if theta < 1e-10:
        return zeros(3)

    # Extract rotation axis
    K = (R - R.T) / (2 * sin(theta))
    theta_hat = array([K[2, 1], K[0, 2], K[1, 0]])
    return theta * theta_hat / norm(theta_hat) if norm(theta_hat) > 0 else zeros(3)

def angular_error_between_matrices(R_true: ndarray, R_recon: ndarray) -> float:
    """ Calculate the angular distance error between two rotation matrices. """
    R_diff = R_true.T @ R_recon
    tr = trace(R_diff)
    cos_theta = (tr - 1.) / 2.
    return arccos(clip(cos_theta, -1., 1.))

class ImuModel:
    def __init__(self, gyro_params: IMUNoiseParams, accel_params: IMUNoiseParams, sigma_m: float):
        """ Initialize IMU model with noise parameters and misalignment.

        Args:
            gyro_params: Parameters for gyroscope
            accel_params: Parameters for accelerometer
            sigma_m: Misalignment standard deviation (rad)
        """
        self.gyro_params = gyro_params
        self.accel_params = accel_params
        self._M_misalignment = rotation_matrix_from_vector(normal(0, sigma_m, 3))  # Misalignment matrix
        self._bias_gyro = normal(0, gyro_params.sigma_to, 3)  # Initial gyro bias
        self._bias_accel = normal(0, accel_params.sigma_to, 3)  # Initial accel bias
        self._previous_timestamp: float | None = None
        self._previous_R_inertial_from_body: Annotated[ndarray | None, (3, 3)] = None
        self._previous_R_inertial_from_body: Annotated[ndarray | None, (3, 3)] = None
        self._previous_v_inertial: Annotated[ndarray | None, (3,)] = None
    ####

    @classmethod
    def update_bias(cls, params: IMUNoiseParams, current_bias: ndarray, dt: float) -> ndarray:
        """ Update bias using Gauss-Markov model. """
        if dt <= 0.:
            return current_bias
        ####
        eta_b = normal(0, params.sigma_b * sqrt(1 - exp(-2 * dt / params.tau)), 3)
        return exp(-dt / params.tau) * current_bias + eta_b
    ####

    @classmethod
    def generate_noise(cls, params: IMUNoiseParams, dt: float) -> ndarray:
        """
        Generate white noise for rate signals.

        Args:
            params (IMUNoiseParams): Noise parameters
            dt (float): Time step (s)

        Returns:
            ndarray: White noise vector
        """
        if dt <= 0.:
            return zeros((3,))
        ####
        return normal(0, params.sigma_wn / sqrt(dt), size=(3,))
    ####

    @classmethod
    def apply_nonlinearity(cls, true_signal: ndarray, params: IMUNoiseParams) -> ndarray:
        """ Apply scale factor error and nonlinearity. """
        norm_signal = float(norm(true_signal)) / params.x_max
        scale_factor = 1 + params.epsilon + params.alpha * (norm_signal ** 2)
        return true_signal * scale_factor
    ####

    def step(self, timestamp: float, R_inertial_from_body, v_inertial: ndarray) -> ImuMeasurement:
        """ Generate IMU measurements from true orientation and velocity states. """
        if self._previous_timestamp is None:
            self._previous_timestamp = timestamp
            self._previous_v_inertial = v_inertial
            self._previous_R_inertial_from_body = R_inertial_from_body
        ####
        dt = timestamp - self._previous_timestamp
        R2 = R_inertial_from_body
        R1 = self._previous_R_inertial_from_body

        v2 = v_inertial
        v1 = self._previous_v_inertial

        # Calculate true omega from orientation change
        R_diff = R2 @ R1.T  # Relative rotation from R1 to R2
        delta_theta_true = rotation_vector_from_matrix(R_diff)
        omega_true = delta_theta_true / dt if dt > 0 else zeros(3)

        # Calculate true acceleration in body frame
        delta_v_inertial = v2 - v1
        a_true_inertial = delta_v_inertial / dt if dt > 0 else zeros(3)
        a_true_body = R1.T @ a_true_inertial  # Transform to body frame 1

        # Update biases
        self._bias_gyro = self.update_bias(self.gyro_params, self._bias_gyro, dt)
        self._bias_accel = self.update_bias(self.accel_params, self._bias_accel, dt)

        # Generate white noise
        noise_omega = self.generate_noise(self.gyro_params, dt)
        noise_a = self.generate_noise(self.accel_params, dt)

        # Apply nonlinearity and scale factor
        omega_scaled = self.apply_nonlinearity(omega_true, self.gyro_params)
        a_scaled = self.apply_nonlinearity(a_true_body, self.accel_params)

        # Apply misalignment
        omega_meas = self._M_misalignment @ omega_scaled + self._bias_gyro + noise_omega
        a_meas = self._M_misalignment @ a_scaled + self._bias_accel + noise_a

        # Integrate to get delta quantities
        delta_theta = omega_meas * dt
        delta_v = a_meas * dt

        # Update previous states
        self._previous_timestamp = timestamp
        self._previous_R_inertial_from_body = R_inertial_from_body
        self._previous_v_inertial = v_inertial

        return ImuMeasurement(timestamp=timestamp, delta_theta=delta_theta, delta_v=delta_v)
    ####
####

# Simple ballistic spinning trajectory simulation
@dataclass
class SimpleTrajectory:
    time: Annotated[ndarray, (int,)]
    position: Annotated[ndarray, (int, 3)]
    velocity: Annotated[ndarray, (int, 3)]
    orientation: Annotated[ndarray, (int, 3, 3)]
####

def simulate_trajectory(duration: float, dt: float) -> SimpleTrajectory:
    """ Simulate a ballistic spinning trajectory. """
    n_steps = int(duration / dt)
    g = 9.81  # m/s^2
    omega_spin = 0.1  # rad/s spinning rate

    pos = zeros((n_steps, 3))
    vel = zeros((n_steps, 3))
    R = zeros((n_steps, 3, 3))  # 3D array for rotation matrices
    R[0] = eye(3)  # Initial rotation matrix
    gravity = array([0., 0., g])

    for k in range(n_steps - 1):
        # Simple ballistic motion with constant spin
        vel[k + 1] = vel[k] - gravity * dt
        pos[k + 1] = pos[k] + vel[k] * dt

        # Update rotation due to spin around z-axis
        theta = omega_spin * dt
        R_new = array([
            [cos(theta), -sin(theta), 0],
            [sin(theta), cos(theta), 0],
            [0, 0, 1]
        ]) @ R[k]
        R[k + 1] = R_new
    ####
    return SimpleTrajectory(time=arange(0, duration, dt), position=pos, velocity=vel, orientation=R)
####

def run_simulation(trajectory: SimpleTrajectory, imu: ImuModel) -> SimpleTrajectory:
    time = trajectory.time
    R_true = trajectory.orientation
    pos_true = trajectory.position
    vel_true = trajectory.velocity

    pos_recon = zeros_like(pos_true)
    vel_recon = zeros_like(vel_true)
    R_recon = zeros((len(time), 3, 3))

    R_recon[0] = R_true[0]
    vel_recon[0] = vel_true[0]
    pos_recon[0] = pos_true[0]

    # Prime the IMU states
    imu.step(
        timestamp=float(time[0]),
        R_inertial_from_body=R_true[0],
        v_inertial=vel_true[0],
    )
    for k in range(1, len(time)):
        measurement = imu.step(
            timestamp=float(time[k]),
            R_inertial_from_body=R_true[k],
            v_inertial=vel_true[k],
        )
        delta_theta = measurement.delta_theta
        delta_v = measurement.delta_v

        dt = time[k] - time[k - 1]
        # Update rotation using proper rotation matrix from delta_theta
        R_delta = rotation_matrix_from_vector(delta_theta)
        R_recon[k] = R_recon[k - 1] @ R_delta

        # Reconstruct states (simple integration)
        vel_recon[k] = vel_recon[k - 1] + R_recon[k] @ delta_v
        pos_recon[k] = pos_recon[k - 1] + vel_recon[k] * dt
    ####
    return SimpleTrajectory(
        time=time, position=pos_recon, velocity=vel_recon,
        orientation=R_recon,
    )
####

@dataclass
class TrajectoryError:
    time: Annotated[ndarray, (int,)]
    position: Annotated[ndarray, (int,)]
    velocity: Annotated[ndarray, (int,)]
    total_angle: Annotated[ndarray, (int,)]
####

def calculate_trajectory_error(trajectory1: SimpleTrajectory, trajectory2: SimpleTrajectory) -> TrajectoryError:
    pos_error = norm(trajectory1.position - trajectory2.position, axis=1)
    vel_error = norm(trajectory1.velocity - trajectory2.velocity, axis=1)

    # Calculate total angular distance error
    angular_error = zeros(len(trajectory1.time))
    for k in range(len(angular_error)):
        angular_error[k] = angular_error_between_matrices(trajectory1.orientation[k], trajectory2.orientation[k])
    ####
    return TrajectoryError(
        time=trajectory1.time,
        position=pos_error,
        velocity=vel_error,
        total_angle=angular_error,
    )
####

def plot_trajectory_errors(trajectory_errors: Sequence[TrajectoryError]) -> None:
    # NOTE: Assumes that they are all on the same time vector
    num_trajectories = len(trajectory_errors)
    time = trajectory_errors[0].time
    pos_error = stack([te.position for te in trajectory_errors], axis=-1)
    vel_error = stack([te.velocity for te in trajectory_errors], axis=-1)
    angular_error = rad2deg(stack([te.total_angle for te in trajectory_errors], axis=-1))

    pos_mean = mean(pos_error, axis=-1)
    vel_mean = mean(vel_error, axis=-1)
    angular_mean = mean(angular_error, axis=-1)

    pos_std = std(pos_error, axis=-1)
    vel_std = std(vel_error, axis=-1)
    angular_std = std(angular_error, axis=-1)

    # Plot errors
    plt.figure(figsize=(12, 8))

    plt.subplot(3, 1, 1)
    plt.plot(time, pos_error)
    if num_trajectories > 1:
        plt.plot(time, pos_mean, label=r'$\mu$', color='k', linewidth=2)
        plt.plot(time, pos_mean + pos_std, label=r'$\mu\pm\sigma$', color='k', linewidth=2, linestyle='--')
        plt.plot(time, pos_mean - pos_std, color='k', linewidth=2, linestyle='--')
    ####
    plt.xlabel('Time (s)')
    plt.ylabel('Error (m)')
    plt.legend(loc='upper left')
    plt.title('Position Reconstruction Error')
    plt.grid(True)

    plt.subplot(3, 1, 2)
    plt.plot(time, vel_error)
    if num_trajectories > 1:
        plt.plot(time, vel_mean, label=r'$\mu$', color='k', linewidth=2)
        plt.plot(time, vel_mean + vel_std, label=r'$\mu\pm\sigma$', color='k', linewidth=2, linestyle='--')
        plt.plot(time, vel_mean - vel_std, color='k', linewidth=2, linestyle='--')
    ####
    plt.xlabel('Time (s)')
    plt.ylabel('Error (m/s)')
    plt.legend(loc='upper left')
    plt.title('Velocity Reconstruction Error')
    plt.grid(True)

    plt.subplot(3, 1, 3)
    plt.plot(time, angular_error)
    if num_trajectories > 1:
        plt.plot(time, angular_mean, label=r'$\mu$', color='k', linewidth=2)
        plt.plot(time, angular_mean + angular_std, label=r'$\mu\pm\sigma$', color='k', linewidth=2, linestyle='--')
        plt.plot(time, angular_mean - angular_std, color='k', linewidth=2, linestyle='--')
    ####
    plt.xlabel('Time (s)')
    plt.ylabel('Error (deg)')
    plt.legend(loc='upper left')
    plt.title('Orientation Reconstruction Error')
    plt.grid(True)

    plt.tight_layout()
####

def main() -> None:
    # Define parameters
    duration = 10.0  # s
    sigma_m = deg2rad(.2)  # rad

    gravity = 9.81

    # Noise parameters for gyro and accel
    gyro_params = IMUNoiseParams(
        sigma_wn=0.01,  # rad/s/sqrt(Hz)
        sigma_to=0.001,  # rad/s
        sigma_b=0.0001,  # rad/s
        tau=100.0,  # s
        x_max=deg2rad(500.0),  # rad/s
        epsilon=2e-4,  # 200 ppm
        alpha=1e-6,
    )

    accel_params = IMUNoiseParams(
        sigma_wn=0.01,  # m/s^2/sqrt(Hz)
        sigma_to=0.01,  # m/s^2
        sigma_b=0.001,  # m/s^2
        tau=100.0,  # s
        x_max=16.0 * gravity,  # m/s^2
        epsilon=2e-4,  # 200 ppm
        alpha=1e-6,  # unitless
    )

    # Initialize IMU model
    def generate_imu() -> ImuModel:
        return ImuModel(gyro_params, accel_params, sigma_m)
    ####

    # Simulate trajectory
    dt = 0.01
    trajectory = simulate_trajectory(duration, dt=dt)

    num_monte = 30
    reconstructed_trajectories = []
    for num_monte in range(num_monte):
        imu = generate_imu()
        reconstructed_trajectory = run_simulation(trajectory, imu)
        reconstructed_trajectories.append(reconstructed_trajectory)
    ####
    traj_errors = [calculate_trajectory_error(trajectory, rt) for rt in reconstructed_trajectories]
    plot_trajectory_errors(traj_errors)

    plt.show()
    print("Simulation completed. Error plots generated.")
####

if __name__ == "__main__":
    main()
####
