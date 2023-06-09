#!/usr/bin/env python3
import time
import numpy as np
import rospy
# import rospkg
import tf2_ros
import tf
import threading
from nav_msgs.msg import Odometry
from tf.transformations import euler_from_quaternion
from sensor_msgs.msg import PointCloud
from geometry_msgs.msg import Twist
import message_filters
from nav_msgs.msg import Odometry
import sys
import open3d
import os


cwd = os.getcwd()

is_received = False

obstacle_points_vehicle = None

min_dis_points_vehicle = None

pointcloud_mutex = threading.Lock()
odom_mutex = threading.Lock()
publish_traj_mutex = threading.Lock()

num_laser_points = 720
num_down_sampled = 200

xyz = np.random.rand(720, 3)


jackal_velocities = []
original_pointclouds = []
downsampled_points = []
jackal_orientation = []
jackal_odometry = []


def odomCallback(odom_msg, pointcloud, cmd_vel):

    global is_received, odom_mutex, obstacle_points_vehicle, xyz, num_laser_points, num_down_sampled, jackal_velocities

    tf_listener = buffer.lookup_transform(
        odom_msg.header.frame_id, pointcloud.header.frame_id, rospy.Time(), rospy.Duration(1.0))
    trans = [tf_listener.transform.translation.x,
             tf_listener.transform.translation.y, tf_listener.transform.translation.z]
    rot = [tf_listener.transform.rotation.x, tf_listener.transform.rotation.y,
           tf_listener.transform.rotation.z, tf_listener.transform.rotation.w]
    transformation_matrix = tf.transformations.concatenate_matrices(
        tf.transformations.translation_matrix(trans), tf.transformations.quaternion_matrix(rot))


    odom_mutex.acquire()

    jackal_q = odom_msg.pose.pose.orientation
    jackal_list = [jackal_q.x, jackal_q.y, jackal_q.z, jackal_q.w]
    (jackal_roll, jackal_pitch, jackal_yaw) = euler_from_quaternion(jackal_list)

    x_vel = cmd_vel.linear.x
    ang_z_vel = cmd_vel.angular.z

    # getting original pointclouds data
    msg_len = len(pointcloud.points)
    pointcloud_mutex.acquire()
    increment_value = 1
    inner_counter = 0
    x_obs_pointcloud_vehicle = np.ones((num_laser_points, 1)) * 1000
    y_obs_pointcloud_vehicle = np.ones((num_laser_points, 1)) * 1000
    for nn in range(0, msg_len, increment_value):
        x_obs_pointcloud_vehicle[inner_counter] = pointcloud.points[nn].x
        y_obs_pointcloud_vehicle[inner_counter] = pointcloud.points[nn].y
        inner_counter += 1

    xyz[:, 0] = x_obs_pointcloud_vehicle.flatten()
    xyz[:, 1] = y_obs_pointcloud_vehicle.flatten()
    xyz[:, 2] = 1

    # replace all points greater than 300m
    idxes = np.argwhere(xyz[:, :] >= 300)
    xyz[idxes, 0] = xyz[0, 0]
    xyz[idxes, 1] = xyz[0, 1]

    # now transform pointclouds
    xyz_transformed = np.hstack((xyz, np.ones((xyz.shape[0], 1))))
    xyz_transformed = np.dot(transformation_matrix, xyz_transformed.T).T[:, :3]

    start_time = time.time()
    # downsample transformed pointclouds
    pcd = open3d.geometry.PointCloud()
    pcd.points = open3d.utility.Vector3dVector(xyz_transformed)
    downpcd = pcd.voxel_down_sample(voxel_size=0.9)
    
    downpcd_array = np.asarray(downpcd.points)

    num_down_sampled_points = downpcd_array[:, 0].shape[0]

    # create 1D array of size 200
    x_obs_down_sampled = np.ones((200, 1)) * 1000
    y_obs_down_sampled = np.ones((200, 1)) * 1000
    x_obs_down_sampled[0:num_down_sampled_points, 0] = downpcd_array[:, 0]
    y_obs_down_sampled[0:num_down_sampled_points, 0] = downpcd_array[:, 1]
    obstacle_points_vehicle = np.hstack(
        (x_obs_down_sampled, y_obs_down_sampled))

    pointcloud_mutex.release()
    inner_counter = 0

    # replace all points greater than 300m
    idxes = np.argwhere(obstacle_points_vehicle[:, :] >= 300)
    obstacle_points_vehicle[idxes, 0] = obstacle_points_vehicle[0, 0]
    obstacle_points_vehicle[idxes, 1] = obstacle_points_vehicle[0, 1]
    obstacle_points_vehicle[:, 0] = obstacle_points_vehicle[:,
                                                            0] - odom_msg.pose.pose.position.x
    obstacle_points_vehicle[:, 1] = obstacle_points_vehicle[:,
                                                            1] - odom_msg.pose.pose.position.y
    
    print("Downsample Computation Time: ", time.time() - start_time)

    # gather data
    downsampled_points.append(obstacle_points_vehicle)
    jackal_odometry.append([odom_msg.pose.pose.position.x, odom_msg.pose.pose.position.y, jackal_yaw])
    jackal_velocities.append([x_vel, ang_z_vel])
    
    odom_mutex.release()


if __name__ == "__main__":

    rospy.init_node('data_collection')
    args = sys.argv[1:]
    world_no = args[0]  # world number
    rospy.loginfo("data collection initialized!")
    # rospack = rospkg.RosPack()

    buffer = tf2_ros.Buffer()
    tf_listener = tf2_ros.TransformListener(buffer)

    jackal_pointcloud_sub = message_filters.Subscriber(
        '/pointcloud', PointCloud)
    jackal_odom_sub = message_filters.Subscriber(
        '/odometry/filtered', Odometry)
    jackal_vel_sub = message_filters.Subscriber('/cmd_vel', Twist)

    ts = message_filters.ApproximateTimeSynchronizer(
        [jackal_odom_sub, jackal_pointcloud_sub, jackal_vel_sub], 1, 1, allow_headerless=True)
    ts.registerCallback(odomCallback)

    rospy.spin()
    # print("\n",jackal_odometry)

    directory = "data/" + str(world_no) + "/"
    if not os.path.exists(directory):
        os.makedirs(directory)

    fname1 = os.path.join(cwd, directory + "jackal_odometry_0.npy")
    fname2 = os.path.join(cwd, directory + "downsampled_pointclouds_0.npy")
    fname3 = os.path.join(cwd, directory + "jackal_velocitiies_0.npy")

    if os.path.isfile(fname1):
        # generate new file numbered if it already exists
        count = 1
        while os.path.isfile(f"{fname1.split('.')[0]}_{count}.npy"):
            count += 1
        fname1 = f"{fname1.split('.')[0]}_{count}.npy"

    if os.path.isfile(fname2):
        # generate new file numbered if it already exists
        count = 1
        while os.path.isfile(f"{fname2.split('.')[0]}_{count}.npy"):
            count += 1
        fname2 = f"{fname2.split('.')[0]}_{count}.npy"

    np.save(fname1, jackal_odometry)
    np.save(fname2, downsampled_points)
    np.save(fname3, jackal_velocities)
    print(np.array(downsampled_points).shape)
    print("Files were saved")

    rospy.signal_shutdown(rospy.loginfo_once("Node is shutting down"))
