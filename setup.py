from setuptools import find_packages, setup

package_name = "mvp_mission_bebop"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="jv",
    maintainer_email="dev@example.com",
    description="Missão autônoma MVP para Parrot Bebop 2 via Nectar SDK",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "mission = mvp_mission_bebop.mission:main",
            "flight_controller = mvp_mission_bebop.flight_controller:main",
        ],
    },
)
